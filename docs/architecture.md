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
| `KnowledgeItem` | vertex, extends `Node` | extracted item; `embedding` for vector search; review state; `status` / `declared_status` for decisions and risks; `health_signals`, `review_due`, `verified_at` / `verified_by` ([knowledge health](knowledge-health.md)) |
| `CONTAINS` | edge | artifact → item |
| `RELATED_TO` | edge | item → item cross-link, with `score` |
| `LINK` | edge | any node → node, from OKF import; `type` holds the relation label |
| `EVOLVES` | edge | item → item, newer → older; `kind` is `supersedes`, `reverses`, `amends`, `realizes`, `mitigates` or `learned_from` |
| `User` | document | `role` (`admin` / `member`), `username_key` (unique, case-folded), `last_active_at`, `last_login_at`, chosen `llm_model` |
| `GroupSpace` | document | a group: name, creator, `last_active_at` |
| `Membership` | document | user ↔ group: `role` (`admin` for the creator, `member` otherwise) and `status` (`invited`, `active`, `declined`, `left`, `removed`) |
| `ItemEvent` | document | an item's history: reviewed, edited, linked, unlinked, status changes, health signals, confirmations, review dates, with `actor` |
| `Connector` | document | a source's settings (credentials encrypted) and its last sync result |
| `SyncedDocument` | document | one document a connector fetched → the artifact it became, with a content hash |
| `Playbook`, `QueryLog` | documents | playbooks; one log entry per GraphRAG question |
| `Meta` | document | the embedding model and dimension, the first admin, migration markers, the last retention run |

**`user_id` means "owning space".** On every vertex, edge and per-space document, `user_id` is the id of the space the data belongs to: a user's personal space (whose id *is* the user id) or a group. Data created before groups existed is therefore already in its owner's personal space, with no migration.

**Writes are transactional.** Each request gets a session: the first write opens an ArcadeDB transaction, `commit()` ends it, and anything uncommitted is rolled back when the request finishes. For example, an ingest's artifact, items and edges are saved together or not at all.

**Deletes cascade.** Deleting an artifact deletes its items, and deleting any vertex removes its edges, so nothing is left dangling.


## GraphRAG pipeline

`POST /knowledge/graphrag/query` with `{question, top_k, history}` runs these steps in the active space:

1. **Route.** The question is embedded and classified (factual, exploratory, comparative or procedural) by comparing it with example phrases, or by keywords when that's ambiguous. The route only adds a one-line hint to the answer prompt.
2. **Rewrite (optional, `GRAPHRAG_QUERY_REWRITE=on`).** One LLM call rewrites the question into sub-queries plus a hypothetical answer (HyDE), embedded in one batch. Off by default: see *Measured* below. `GRAPHRAG_LLM_ROUTE=on` also asks the LLM to classify ambiguous questions.
3. **Retrieve** several ranked lists:
   - for the question (and the HyDE text, if rewritten): vector search, then a one-hop expansion along `CONTAINS`, `RELATED_TO`, `LINK` and `EVOLVES` edges
   - for each sub-query, if rewritten: vector search
   - for the question: full-text keyword search

   Rejected items are excluded from all of them.
4. **Fuse** the ranked lists with Reciprocal Rank Fusion. Scores are normalised to 0–1.
5. **Rerank (optional, `GRAPHRAG_RERANK=on`).** An LLM rerank, when there are more than 2 × `top_k` candidates. Off by default.
6. **Gate.** If the best fused score is below 0.5, the API returns "not enough relevant information" instead of asking the LLM.
7. **Ground.** Each selected item gets its details (who, why, due, steps…), the title of its source, and the passage of the source it came from.
8. **Generate.** The context entries are numbered, `[1]`, `[2]`…; the LLM cites them by number, and the numbers are mapped back to items (a full id in brackets is accepted too). Only entries that were actually in the context count. If no entry is about the question, the LLM answers exactly "The knowledge base doesn't cover this." and nothing is cited. When an answer names its sources in words without citing them, they're matched by title and marked `inferred` ("matched" in the UI). Superseded or reversed decisions are marked as such, with what replaced them, and the prompt tells the LLM to present the replacement as current.

**Measured.** [`backend/evals/`](../backend/evals/) ingests a fixed corpus and asks fixed questions against a real model; see its README. On llama3.1:8b on a laptop CPU, the rewrite and rerank calls took about 25 s each per question while retrieval already found the right source every time without them, and the model answered "the context doesn't say who" for owners that were on record, because only item titles reached the prompt. Grounding, numbered citations and dropping those calls took answers from 15 to 19 correct out of 19, "not covered" replies to answerable questions from 4 to 0, and the median answer time from 78 s to 13 s.

The response includes `route`, `sub_queries`, `retrieval_mode`, `context_nodes`, `citations` (with each item's status), and `timings_ms` per stage. Every query is logged as a `QueryLog` document.

**Vector search across spaces.** ArcadeDB's vector indexes cover every space's vectors and can't be pre-filtered. So a query first over-fetches nearest neighbours and keeps the active space's own. If that yields too few (typical for a space that holds a small share of the data), it falls back to an exact cosine scan of just that space's vectors. Results are always the space's true nearest items.
