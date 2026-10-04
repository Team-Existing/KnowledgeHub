# Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Angular 21 SPA                                              │
│  Hub · Search · Review · Decisions · Playbooks · Sources ·   │
│  Groups · GraphRAG · Models · Users (server admins)          │
│  space switcher → X-Space header; authInterceptor adds JWT   │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP (JWT + X-Space)
┌───────────────────────────▼──────────────────────────────────┐
│  FastAPI                                                     │
│  routers/      auth · admin · groups · models · health ·     │
│                knowledge · lineage · connectors · graphrag   │
│  spaces.py     X-Space → the space a request acts in         │
│  rbac.py       roles → permissions    crypto.py  credentials │
│  activity.py   last-active tracking   usernames.py  rules    │
│  coordination.py  locks + dispatch (Redis or in-process)     │
│  worker.py     Celery tasks: connector syncs, clean-up       │
│  services/     artifact_pipeline (extract → persist → index) │
│                graphrag · llm_extraction · file_ingestion ·  │
│                lineage · retention · connectors/ (folder,    │
│                git_adr, github, jira, linear, sync)          │
│  arcadedb.py   HTTP client: one node or a cluster, failover  │
│  repositories/ ArcadeDB SQL, one class per type              │
│                + search (full-text) + graph (vector/graph)   │
│                                                              │
│  Ollama (LLM)            sentence-transformers (embeddings)  │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP API (app/arcadedb.py)
┌───────────────────────────▼──────────────────────────────────┐
│  ArcadeDB                                                    │
│  Artifact ──CONTAINS──▶ KnowledgeItem ──RELATED_TO──▶ …      │
│  EVOLVES (decision lineage) · LINK (OKF import)              │
│  Vector indexes: KnowledgeItem.embedding, Artifact.summary   │
│  Full-text indexes                                           │
│  Documents: User · GroupSpace · Membership · Connector ·     │
│  SyncedDocument · ItemEvent · Playbook · QueryLog · Meta     │
└──────────────────────────────────────────────────────────────┘
```

## Data model

The schema lives in [`backend/app/schema.py`](../backend/app/schema.py) and is applied idempotently at startup.

| Type | Kind | Notes |
|---|---|---|
| `Node` | vertex supertype | `id` (unique across all nodes), `user_id` = the owning **space** (see below) |
| `Artifact` | vertex, extends `Node` | source document; its LLM summary and summary embedding live here |
| `KnowledgeItem` | vertex, extends `Node` | extracted item; `embedding` for vector search; review state; `status` / `declared_status` for decisions and risks |
| `CONTAINS` | edge | artifact → item |
| `RELATED_TO` | edge | item → item cross-link, with `score` |
| `LINK` | edge | any node → node, from OKF import; `type` holds the relation label |
| `EVOLVES` | edge | item → item, newer → older; `kind` is `supersedes`, `reverses`, `amends`, `realizes`, `mitigates` or `learned_from` |
| `User` | document | `role` (`admin` / `member`), `username_key` (unique, case-folded), `last_active_at`, `last_login_at`, chosen `llm_model` |
| `GroupSpace` | document | a group: name, creator, `last_active_at` |
| `Membership` | document | user ↔ group: `role` (`admin` for the creator, `member` otherwise) and `status` (`invited`, `active`, `declined`, `left`, `removed`) |
| `ItemEvent` | document | an item's history: reviewed, edited, linked, unlinked, status changes, with `actor` |
| `Connector` | document | a source's settings (credentials encrypted) and its last sync result |
| `SyncedDocument` | document | one document a connector fetched → the artifact it became, with a content hash |
| `Playbook`, `QueryLog` | documents | playbooks; one log entry per GraphRAG question |
| `Meta` | document | the embedding model and dimension, the first admin, migration markers, the last retention run |

**`user_id` means "owning space".** On every vertex, edge and per-space document, `user_id` is the id of the space the data belongs to: a user's personal space (whose id *is* the user id) or a group. Data created before groups existed is therefore already in its owner's personal space, with no migration.

**Writes are transactional.** Each request gets a session: the first write opens an ArcadeDB transaction, `commit()` ends it, and anything uncommitted is rolled back when the request finishes. For example, an ingest's artifact, items and edges are saved together or not at all.

**Deletes cascade.** Deleting an artifact deletes its items, and deleting any vertex removes its edges, so nothing is left dangling.


## GraphRAG pipeline

`POST /knowledge/graphrag/query` with `{question, top_k, history}` runs these steps in the active space:

1. **Transform and route, concurrently.** One LLM call rewrites the question into sub-queries plus a hypothetical answer (HyDE). At the same time, the question is embedded and classified (factual, exploratory, comparative or procedural) by comparing it with example phrases. The LLM classifier is used only when that comparison is ambiguous.
2. **Embed** the HyDE document and sub-queries in a single batch.
3. **Retrieve** several ranked lists:
   - for the question and HyDE vectors: vector search, then a one-hop expansion along `CONTAINS`, `RELATED_TO`, `LINK` and `EVOLVES` edges
   - for each sub-query: vector search
   - for the question: full-text keyword search

   Rejected items are excluded from all of them.
4. **Fuse** the ranked lists with Reciprocal Rank Fusion. Scores are normalised to 0–1.
5. **Rerank (optional).** An LLM rerank runs only when there are more than 2 × `top_k` candidates. Set `GRAPHRAG_RERANK=off` to always keep the fusion order.
6. **Gate.** If the best fused score is below 0.5, the API returns "not enough relevant information" instead of asking the LLM.
7. **Generate.** The LLM gets the context entries labelled with their full ids and cites them as `[item_id]`. Only ids that were actually in the context are returned as citations. Superseded or reversed decisions are marked as such, together with the decision that replaced them, and the prompt tells the LLM to present the replacement as current.

The response includes `route`, `sub_queries`, `retrieval_mode`, `context_nodes`, `citations` (with each item's status), and `timings_ms` per stage. Every query is logged as a `QueryLog` document.

**Vector search across spaces.** ArcadeDB's vector indexes cover every space's vectors and can't be pre-filtered. So a query first over-fetches nearest neighbours and keeps the active space's own. If that yields too few (typical for a space that holds a small share of the data), it falls back to an exact cosine scan of just that space's vectors. Results are always the space's true nearest items.
