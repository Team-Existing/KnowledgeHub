# Knowledge Hubs

A **local-first knowledge base** that turns team artifacts — meeting notes, retros, decision logs, documents, web pages, transcripts — into structured, searchable knowledge you can question in plain English.

- **Backend:** FastAPI on **ArcadeDB**, the only data store. Artifacts and knowledge items are graph vertices joined by edges, with vector and full-text indexes in the same database.
- **AI:** one of three local LLMs, run by Ollama (see [Local models](#local-models)), and sentence-transformers for embeddings. Everything runs locally; there are no cloud AI providers.
- **Frontend:** Angular 17 single-page app.
- **Multi-user:** every vertex and edge belongs to a user, and every query is scoped to the signed-in user.

---

## What it does

1. **Ingest.** Paste text, upload a file (PDF, TXT, MD, DOCX; up to `MAX_UPLOAD_MB`), fetch a URL, or submit a transcript, email thread or Slack export.
2. **Extract.** Knowledge items are pulled out of each artifact:

   | Source | Extractor | Item types |
   |---|---|---|
   | Pasted text | Rule-based (keywords) | decision, how-to, lesson, risk, best-practice, checklist |
   | Files and URLs | LLM, chunked (~6,000 chars per chunk, up to 8 chunks). Regex fallback for the whole document if no LLM answers, or per chunk if a reply can't be parsed | decision, action-item, how-to, best-practice, lesson, risk, plus checklists (always regex) |
   | Transcripts, email, Slack | LLM, with a regex fallback | decision, action-item, risk |

   Each item records which extractor produced it (shown as a badge in the review queue).
3. **Review.** Every extracted item starts as `pending`. Accept, reject or edit it in the review queue. Rejected items are never used by GraphRAG.
4. **Search.** Full-text search using ArcadeDB's Lucene indexes:
   - **Items:** title, tags and type.
   - **Artifacts:** title, author, tags and body text. Artifacts matched only in their body rank below metadata matches and come back with a snippet.
5. **Link.** `POST /knowledge/link` connects items from different artifacts whose titles share enough keywords (Jaccard similarity ≥ 0.12), as `RELATED_TO` edges. You trigger it manually.
6. **Ask.** GraphRAG answers questions from your knowledge base with citations to the items it used.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Angular 17 SPA                                              │
│  Hub · Search · Review · GraphRAG · Models                   │
│  authInterceptor adds the JWT; a 401 signs the user out      │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP (JWT)
┌───────────────────────────▼──────────────────────────────────┐
│  FastAPI                                                     │
│  routers/       auth · models · health · knowledge · graphrag │
│  services/      artifact_pipeline (extract → persist → index) │
│                 graphrag · llm_extraction · file_ingestion   │
│  repositories/  ArcadeDB SQL, one class per type             │
│                 + search (full-text) + graph (vector/graph)  │
│                                                              │
│  Ollama (LLM)            sentence-transformers (embeddings)  │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP API (app/arcadedb.py)
┌───────────────────────────▼──────────────────────────────────┐
│  ArcadeDB                                                    │
│  Artifact ──CONTAINS──▶ KnowledgeItem ──RELATED_TO──▶ …      │
│  LINK edges (from OKF import) between any two nodes          │
│  Vector indexes: KnowledgeItem.embedding, Artifact.summary   │
│  Full-text indexes · User / Playbook / QueryLog documents    │
└──────────────────────────────────────────────────────────────┘
```

### Data model

The schema lives in [backend/app/schema.py](backend/app/schema.py) and is applied idempotently at startup.

| Type | Kind | Notes |
|---|---|---|
| `Node` | vertex supertype | `id` (unique across all nodes), `user_id` |
| `Artifact` | vertex, extends `Node` | source document; its LLM summary and summary embedding live here |
| `KnowledgeItem` | vertex, extends `Node` | extracted item; `embedding` for vector search; review state |
| `CONTAINS` | edge | artifact → item |
| `RELATED_TO` | edge | item → item cross-link, with `score` |
| `LINK` | edge | any node → node, from OKF import; `type` holds the relation label |
| `User`, `Playbook`, `QueryLog`, `Meta` | documents | `Meta` records the embedding model and dimension |

**Writes are transactional.** Each request gets a session: the first write opens an ArcadeDB transaction, `commit()` ends it, and anything uncommitted is rolled back when the request finishes. For example, an ingest's artifact, items and edges are saved together or not at all.

**Deletes cascade.** Deleting an artifact deletes its items, and deleting any vertex removes its edges, so nothing is left dangling.

---

## GraphRAG pipeline

`POST /knowledge/graphrag/query` with `{question, top_k, history}` runs these steps:

1. **Transform and route, concurrently.** One LLM call rewrites the question into sub-queries plus a hypothetical answer (HyDE). At the same time, the question is embedded and classified (factual, exploratory, comparative or procedural) by comparing it with example phrases. The LLM classifier is used only when that comparison is ambiguous.
2. **Embed** the HyDE document and sub-queries in a single batch.
3. **Retrieve** several ranked lists:
   - for the question and HyDE vectors: vector search, then a one-hop expansion along `CONTAINS`, `RELATED_TO` and `LINK` edges
   - for each sub-query: vector search
   - for the question: full-text keyword search

   Rejected items are excluded from all of them.
4. **Fuse** the ranked lists with Reciprocal Rank Fusion. Scores are normalised to 0–1.
5. **Rerank (optional).** An LLM rerank runs only when there are more than 2 × `top_k` candidates. Set `GRAPHRAG_RERANK=off` to always keep the fusion order.
6. **Gate.** If the best fused score is below 0.5, the API returns "not enough relevant information" instead of asking the LLM.
7. **Generate.** The LLM gets the context entries labelled with their full ids and cites them as `[item_id]`. Only ids that were actually in the context are returned as citations.

The response includes `route`, `sub_queries`, `retrieval_mode`, `context_nodes`, `citations`, and `timings_ms` per stage. Every query is logged as a `QueryLog` document.

**Vector search across users.** ArcadeDB's vector indexes cover every user's vectors and can't be pre-filtered, so a query first over-fetches nearest neighbours and keeps the user's own. If that yields too few (typical for a user who owns a small share of the data), it falls back to an exact cosine scan of just that user's vectors. Results are always the user's true nearest items.

---

## Local models

Knowledge Hubs supports exactly three LLMs, all downloaded and run locally by Ollama:

| Model | Ollama tag | Download | RAM |
|---|---|---|---|
| Llama 3.1 8B | `llama3.1:8b` | 4.7 GB | 8 GB+ |
| Mistral 7B | `mistral:7b` | 4.1 GB | 8 GB+ |
| GPT-OSS 20B | `gpt-oss:20b` | 13 GB | 16 GB+ |

The list lives in [backend/app/services/llm_catalog.py](backend/app/services/llm_catalog.py). Other models Ollama may have are ignored.

- **Signing in needs one of the three.** The sign-in and onboarding screens first check what's installed:
  - **None installed:** you get a download screen. Pick a model; the one that suits your RAM is marked recommended. Sign-in only appears once the download finishes.
  - **One already installed:** you go straight to sign-in.
  - **Ollama not running:** the screen says so, with a Retry button.

  The server enforces the same rule: `POST /auth/token` returns 412 if no model is installed and 503 if Ollama is down.
- **Each user picks their model.** In Model Manager (**Models** in the nav) you can download the other models, switch to any downloaded one, or remove one.
  - Your choice is saved on your account, so it applies to your future sessions.
  - It's used for every LLM call you make: extraction, summaries, GraphRAG, and connector syncs you start.
  - If your model disappears (removed in Model Manager or with `ollama rm`), you're moved to another installed one.
- **At least one model always stays installed.** Removing the last one is refused.

---

## Quick start

Requires Python 3.11, Node 18+, [Ollama](https://ollama.com) (running) and ArcadeDB.

```bash
# 1. ArcadeDB (or download the server from https://github.com/ArcadeData/arcadedb/releases; needs Java 21)
docker run -d --name arcadedb -p 2480:2480 \
  -e JAVA_OPTS="-Darcadedb.server.rootPassword=change-me" arcadedata/arcadedb:26.9.1

# 2. configuration
cd backend
cp .env.example .env          # set SECRET_KEY and ARCADEDB_PASSWORD

# 3. local LLM: nothing to do. If none of the three models is installed, the app's
#    sign-in screen downloads one for you (or: ollama pull llama3.1:8b)

# 4. backend  (Windows shown; on macOS/Linux: source .venv/bin/activate)
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000   # creates the database and schema on first start

# 5. frontend (dev server proxies /auth, /knowledge, /models, /health to :8000)
cd ../frontend && npm install && npm start
# → http://localhost:4200
```

The first embedding call loads the sentence-transformers model, which must already be in the local cache (the app runs with `TRANSFORMERS_OFFLINE=1`).

### Migrating from the SQLite version

Earlier versions stored data in SQLite (`backend/data/knowledge.db`), optionally mirrored to Neo4j. To copy it into ArcadeDB:

```bash
cd backend
python -m scripts.migrate_sqlite_to_arcadedb --sqlite data/knowledge.db
```

What the script does:

- **Reads only:** the SQLite file is opened read-only, and the target database must be empty.
- **Keeps your data intact:** ids, password hashes (so logins keep working), review state and embeddings carry over.
- **Converts relationships into edges:** relationship rows become `CONTAINS` / `LINK` edges, and cross-links become `RELATED_TO` edges.
- **Skips broken links:** edges whose endpoints no longer exist are skipped and reported. The old storage could leave orphaned relationship rows behind.
- **Checks itself:** it compares row counts at the end and exits non-zero on any mismatch.

Neo4j held only a copy of the SQLite data, so there's nothing to migrate from it.

### Configuration (`backend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | insecure placeholder | JWT signing key. **Set this.** |
| `ARCADEDB_URL` | `http://localhost:2480` | ArcadeDB server |
| `ARCADEDB_DATABASE` | `knowledge_hubs` | Database name; created on first start |
| `ARCADEDB_USER`, `ARCADEDB_PASSWORD` | `root`, — | ArcadeDB credentials |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server |
| `OLLAMA_MODEL` | `llama3.1:8b` | Preferred model for users who haven't chosen one yet (if installed) |
| `OLLAMA_NUM_CTX` | `8192` | Context window requested from Ollama. Lower it on small machines |
| `LOCAL_EMBED_MODEL` | `all-MiniLM-L6-v2` | sentence-transformers model (see below) |
| `MAX_UPLOAD_MB` | `20` | Cap on request bodies, uploaded files and fetched pages |
| `GRAPHRAG_RERANK` | `on` | `off` disables the LLM rerank step |
| `LLM_PROVIDER`, `EMBEDDING_PROVIDER` | `ollama`, `local` | Read but not used: those are the only implementations |

**Changing the embedding model.** Vector indexes are sized to the model's dimension.
- **Different dimension:** on startup the app rebuilds the indexes, clears the stored vectors and logs a warning.
- **Same dimension:** it only warns, because the old vectors are no longer comparable with new ones.

Either way, run **Re-embed knowledge base** in Model Manager (`POST /knowledge/reembed`) to recompute the vectors.

---

## API

All endpoints except `/auth/*`, `GET /health`, `GET /models/catalog` and `POST /models/bootstrap-install` need `Authorization: Bearer <token>`.

| Group | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/token` (form fields `username`, `password`; token valid for 8 h) |
| Ingest | `POST /knowledge/artifacts` (text), `/artifacts/upload` (multipart), `/artifacts/url`, `/artifacts/transcript` |
| Artifacts | `PUT /knowledge/artifacts/{id}` (a content change re-extracts items), `DELETE /knowledge/artifacts/{id}` |
| Items | `GET/PUT/DELETE /knowledge/items/{id}`, `GET /knowledge/{ref}` (by id, id suffix or title) |
| Browse | `GET /knowledge` (everything), `GET /knowledge/graph` |
| Review | `GET /knowledge/review`, `PATCH /knowledge/review/{id}` |
| Search | `GET /knowledge/search?q=&type=&source_type=&tag=&limit=` |
| Links | `POST /knowledge/link`, `GET /knowledge/links` |
| GraphRAG | `POST /knowledge/graphrag/query`, `POST /knowledge/reembed` |
| Playbooks | `POST /knowledge/playbooks` |
| OKF | `GET /knowledge/okf/export`, `POST /knowledge/okf/import` |
| Models (public) | `GET /models/catalog` (which of the three are installed), `POST /models/bootstrap-install` (first download; only while none is installed) |
| Models | `GET /models/status`, `/models/local`, `/models/system-info`; `POST /models/install` (streamed progress), `/models/remove`, `/models/set-default` (switch your model) |
| Health | `GET /health` (reports whether ArcadeDB is reachable) |

Interactive docs are at `http://localhost:8000/docs`.

---

## Security notes

- **Accounts.** Passwords are hashed with bcrypt. Tokens are HS256 JWTs signed with `SECRET_KEY` and expire after 8 hours. In the frontend, `authInterceptor` attaches the token to API requests only, and any 401 from the API signs the user out and redirects to `/login`.
- **Isolation.** Artifact and item ids are hashes that include the user id, so two users ingesting the same document get separate nodes. Every query filters by `user_id`, edges are only ever created between the same user's nodes, and the test suite checks both.
- **Query safety.** All values are passed to ArcadeDB as bound parameters. Full-text queries are rebuilt from word characters only, so user input can't inject Lucene operators.
- **URL fetching.** Only `http(s)` is allowed. Hosts that resolve to private, loopback, link-local (including cloud metadata) or reserved addresses are rejected, and every redirect hop is checked again.
- **Uploads.** Oversized bodies get a 413 before they're buffered. PDF and DOCX are parsed from Starlette's spooled temp file rather than read into memory. Legacy `.doc` is rejected; save it as `.docx`.
- **Rendering.** GraphRAG answers are HTML-escaped before citation badges are inserted.

---

## Tests

```bash
# backend: 61 tests against a real ArcadeDB (LLM and embeddings are faked; no Ollama needed)
cd backend && pip install -r requirements-dev.txt
KH_TEST_ARCADEDB_PASSWORD=<root password> pytest
#   optional: KH_TEST_ARCADEDB_URL (default http://localhost:2480), KH_TEST_ARCADEDB_USER (default root)

# frontend: Karma + Jasmine (needs Chrome)
cd frontend && npm test -- --watch=false --browsers=ChromeHeadless
```

**Backend tests and your data:** they create a throwaway database (`kh_test_…`) and drop it afterwards, so they never touch your data. If ArcadeDB isn't reachable, every test is skipped and the reason is shown.

**Backend coverage:**
- every ingestion path, including LLM fallbacks, file formats, limits, URL safety and transactional rollback
- per-user isolation across all endpoints, including vector search among other users' data and edges never crossing users
- the GraphRAG pipeline with a mocked LLM

**Frontend coverage:** the auth interceptor and error handling.

---

## Known limitations

- **One LLM, one embedding model.** Only Ollama and local sentence-transformers are implemented.
- **Models are per machine, choices are per user.** Everyone on one server shares the same Ollama, so removing a model affects every account. Anyone using it is moved to another installed model.
- **Full-text search doesn't fold accents.** The default analyzer means `cafe` won't match "Café".
- **Search slows on very broad queries.** On 50,000 items a precise query takes about 75 ms, but a one-word query matching 30,000+ items takes about 300 ms, mostly spent counting the total.
- **Extracted text isn't size-capped.** Uploads and URLs are capped by `MAX_UPLOAD_MB`, but the text extracted from them isn't (pasted text is capped at 100,000 characters). LLM extraction reads at most 8 chunks.
- **Writes are one HTTP round trip per statement.** An ingest of N items makes roughly 3N calls to ArcadeDB. That's fine on the same machine, but slower if ArcadeDB is remote.
- **URL fetching is exposed to DNS rebinding.** Addresses are checked, then httpx resolves the host again when connecting, so a hostile DNS server can return a different address the second time.
- **No OKF UI yet.** Import and export are API-only.
