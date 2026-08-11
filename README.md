# Knowledge Hubs

A **local-first knowledge graph engine** that converts raw team artifacts — meeting notes, retros, decision logs, project writeups — into structured, queryable operational memory. No SaaS dependency. No data leaves your machine unless you explicitly opt in to OpenAI.

Under the hood: a **FastAPI** extraction pipeline writes to **Neo4j** (graph + vector store) with **SQLite** as a hot-standby fallback, served through an **Angular 17** SPA. LLM and embedding layers are fully swappable at runtime via env vars — default stack runs 100% offline with **Ollama** + **sentence-transformers**.

---

## What It Actually Does

1. **Ingest** — paste text, upload PDF/TXT/MD, fetch a URL (raw HTML, no tag stripping), or pipe in a transcript/email/Slack thread (all three go through the same LLM extraction prompt — `source_type` is a label, not a parser).
2. **Extract** — rule-based + LLM extraction identifies decisions, risks, lessons, best practices, checklists, and how-to items.
3. **Persist** — artifacts and extracted items land in Neo4j as nodes with typed edges (`CONTAINS`, `RELATED_TO`). Every item is also embedded and stored for vector search.
4. **Link** — cross-source linker runs cosine similarity across all items and surfaces related knowledge from different artifacts automatically.
5. **Query** — GraphRAG pipeline retrieves relevant graph context and generates grounded answers with citations. Ask in plain English.
6. **Review** — human-in-the-loop queue: accept or reject extracted items before they enter the active knowledge base.

---

## Architecture

```
Knowledge Hubs
├── backend/                  FastAPI + extraction pipeline
│   └── app/
│       ├── main.py           Routes, CRUD, ingestion orchestration
│       ├── store.py          Neo4j persistence layer
│       ├── db.py             SQLite models (SQLAlchemy async)
│       ├── auth.py           JWT authentication
│       └── services/
│           ├── embeddings.py          sentence-transformers / OpenAI
│           ├── llm_client.py          Ollama / OpenAI router
│           ├── llm_extraction.py      LLM transcript extraction
│           ├── graphrag.py            GraphRAG retrieval + generation
│           ├── knowledge_extraction.py  Rule-based extraction
│           ├── cross_source_linker.py   Cosine similarity cross-linking
│           ├── graph_builder.py         Visualization payload builder
│           ├── neo4j_graph.py           Neo4j graph + vector index
│           └── okf.py                   OKF import/export
└── frontend/                 Angular 17 SPA
    └── src/app/pages/
        ├── knowledge/         Ingest, browse, graph, artifact CRUD
        ├── knowledge-detail/  Item detail, edit, relationships
        ├── search/            Full-text + filtered search
        ├── review/            Pending item review queue
        ├── graphrag/          GraphRAG chat with conversation history
        ├── workspace-settings/ Provider policy + config management
        ├── model-manager/     Ollama model install/remove/set-default
        └── onboarding/        First-run hardware detection + setup
```

---

## Tech Stack

| Layer | Default | Cloud Upgrade |
|---|---|---|
| LLM | Ollama `llama3.1:8b` | `LLM_PROVIDER=openai` |
| Embeddings | `all-MiniLM-L6-v2` (dim=384) | `EMBEDDING_PROVIDER=openai` (dim=1536) |
| Graph store | Neo4j | — |
| Vector fallback | SQLite cosine search | — |
| Backend | FastAPI + Uvicorn | — |
| Frontend | Angular 17 | — |
| Auth | JWT (8h expiry, PBKDF2 key derivation) | — |

Set `ALLOW_CLOUD_PROVIDERS=false` to hard-lock the entire runtime to local-only — no OpenAI calls possible regardless of workspace config.

---

## Quick Start

```bash
# 1. env
cp .env.example .env          # defaults work offline, no keys needed

# 2. local LLM
ollama pull llama3.1:8b

# 3. backend
cd backend && python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 4. frontend
cd frontend && npm install && npm start
# → http://localhost:4200
```

Or just:

```bash
docker compose up --build
# frontend → http://localhost:3000  |  backend → http://localhost:8000
```

---

## API Surface (condensed)

| Group | Key Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/token` |
| Artifacts | `GET/POST /knowledge`, `POST /knowledge/artifacts/upload`, `/url`, `/transcript` |
| Items | `PUT/DELETE /knowledge/items/{id}` |
| Review | `GET /knowledge/review`, `PATCH /knowledge/review/{id}` |
| Search | `GET /knowledge/search` |
| Graph | `GET /knowledge/graph`, `POST /knowledge/link` |
| GraphRAG | `POST /knowledge/graphrag/query` |
| OKF | `POST /knowledge/okf/import`, `GET /knowledge/okf/export` |
| Workspace | `GET|PATCH /workspace/settings`, `POST /workspace/api-key` |
| Models | `GET /models/local`, `POST /models/install`, `/remove`, `/set-default` |
| Re-embed | `POST /knowledge/reembed` |
| Health | `GET /health`, `GET /health/consistency` |

---

## Picky Questions

**Why Neo4j AND SQLite? Isn't that over-engineered for a local tool?**
Neo4j is the primary store for graph traversal and vector indexing. SQLite is a hot-standby — every embedding write goes to both. If Neo4j is down or absent (`STORAGE_BACKEND=sqlite`), the app degrades gracefully instead of failing. The consistency drift between the two is surfaced via `GET /health/consistency`.

**What happens on a partial delete if Neo4j is unreachable?**
SQLite commits first, then Neo4j cleanup is attempted. If Neo4j fails, the API returns HTTP 207 with a `neo4j_error` field and logs at ERROR. The item is gone from SQLite but may linger as an orphan node in Neo4j — `GET /health/consistency` will surface it.

**Are artifact IDs stable across re-ingestion?**
Yes. IDs are SHA-256 hashes of content, so re-ingesting the same document is fully idempotent — no duplicates, no re-extraction.

**How is the OpenAI API key stored?**
`POST /workspace/api-key` derives a Fernet key from `SECRET_KEY` via PBKDF2 (100k iterations) and stores only the AES-128 ciphertext in `ProviderConfig.config_json`. Plaintext is never written to disk or returned to the client.

**What breaks if I switch embedding providers mid-flight?**
The Neo4j vector index dimension changes (384 → 1536 for OpenAI). Changing the provider in workspace settings automatically triggers `POST /knowledge/reembed`, which re-vectorizes all items and artifact summaries and rebuilds the index. Old vectors are incompatible and will be replaced.

**Is the GraphRAG retrieval semantic or keyword-based?**
Both. The pipeline retrieves candidate nodes via vector similarity (cosine over stored embeddings), then reranks using the LLM before generating a grounded answer with citations. The retrieval mode is visible in the GraphRAG chat UI.

**How does cross-source linking work?**
`POST /knowledge/link` runs pairwise cosine similarity across all knowledge item embeddings from different artifacts. Pairs above a similarity threshold are stored as cross-links and surfaced in item detail views. It's not automatic on ingest — you trigger it explicitly.

**Can this run fully air-gapped?**
Yes. Default stack: Ollama (local LLM) + sentence-transformers (local embeddings) + Neo4j (local graph) + SQLite (local fallback). Zero outbound calls. Set `ALLOW_CLOUD_PROVIDERS=false` to enforce this at the policy level.

**What's OKF and why does it matter?**
Open Knowledge Format — a structured JSON schema for portable knowledge payloads. Import/export endpoints are fully implemented, making workspace migration and air-gapped transfer possible via file. A frontend UI for drag-and-drop OKF transfer is planned.

**How does JWT auth work on the frontend?**
The Angular route guard decodes the `exp` claim on every navigation. If the token is stale (8h expiry), it calls `auth.logout()` and redirects to `/login`. No silent refresh — expired sessions require re-login.
