# Configuration

Where settings go depends on how you run it:

- **Docker, one command** ([`docker-compose.yml`](../docker-compose.yml)): nothing is required. The optional settings are listed at the top of that file; put them in a `.env` file next to it. `SECRET_KEY` and `CREDENTIALS_KEY` are generated on first start unless you set them.
- **From source:** `backend/.env` ([`.env.example`](../backend/.env.example) has every setting with comments).
- **Production stack:** `deploy/.env` ([`deploy/.env.example`](../deploy/.env.example)); Compose passes each container only the ones it needs.

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | — (**required**) | JWT signing key, 32+ random characters. The server won't start without one |
| `ARCADEDB_URL` | `http://localhost:2480` | ArcadeDB server, or several cluster nodes, comma-separated. A node that can't be reached is skipped (connection timeout 3 s); a transaction stays on the node where it began |
| `ARCADEDB_DATABASE` | `knowledge_hubs` | Database name; created on first start |
| `ARCADEDB_USER`, `ARCADEDB_PASSWORD` | `root`, — | ArcadeDB credentials. In production, the dedicated app user `provision` creates |
| `DB_MIGRATE_ON_STARTUP` | `true` | Whether the API applies the schema and migrations at startup. `false` when several instances run (the production stack): `provision` does it once |
| `REDIS_URL` | — | Set = worker mode: syncs and the clean-up run on the Celery worker, and locks live in Redis. Unset = everything runs in the API process (development) |
| `CELERY_VISIBILITY_TIMEOUT` | `7200` | Seconds before a sync whose worker died is re-queued. Keep it longer than your longest sync |
| `CREDENTIALS_KEY` | — | Key(s) that encrypt stored connector credentials; comma-separated for rotation. Without it, connectors that need a token can't be saved |
| `CONNECTOR_ROOTS` | — | Folders that folder / git-ADR connectors may read (`;`-separated on Windows, `:` elsewhere). Empty disables those connectors |
| `ALLOW_REGISTRATION` | `true` | `false` = only server admins create accounts. The first account can always be created |
| `RETENTION_INACTIVE_DAYS` | `365` | Accounts and groups unused this long are deleted with their data |
| `RETENTION_CHECK_HOURS` | `24` | How often the clean-up runs (also once at startup) |
| `RETENTION_ENABLED` | `true` | `false` turns automatic deletion off; admins still see what's due |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server |
| `OLLAMA_MODEL` | `llama3.1:8b` | Preferred model for users who haven't chosen one yet (if installed) |
| `OLLAMA_NUM_CTX` | `8192` | Context window requested from Ollama. Lower it on small machines |
| `LOCAL_EMBED_MODEL` | `all-MiniLM-L6-v2` | sentence-transformers model |
| `MAX_UPLOAD_MB` | `20` | Cap on request bodies, uploaded files and fetched pages |
| `GRAPHRAG_RERANK` | `on` | `off` disables the LLM rerank step |
| `LLM_PROVIDER`, `EMBEDDING_PROVIDER` | `ollama`, `local` | Read but not used: those are the only implementations |

**Changing the embedding model.** Vector indexes are sized to the model's dimension.
- **Different dimension:** on startup the app rebuilds the indexes, clears the stored vectors and logs a warning.
- **Same dimension:** it only warns, because the old vectors are no longer comparable with new ones.

Either way, run **Re-embed knowledge base** on the Models page (`POST /knowledge/reembed`) to recompute the vectors.
