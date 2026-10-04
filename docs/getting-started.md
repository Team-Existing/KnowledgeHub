# Getting started

There are two ways to run Knowledge Hubs on your machine:

- **[With Docker, in one command](#run-it-with-docker)**: the whole app in containers. Best for trying it out or using it.
- **[From source](#run-it-from-source)**: the backend and frontend as local processes with live reload. Best for working on the code.

For a multi-instance setup with TLS, see [Production deployment](deployment.md).

## Run it with Docker

Requires [Docker](https://docs.docker.com/get-docker/) with Compose v2.20+, about 15 GB of free disk space (images, plus 4–13 GB for a model) and 8 GB+ of RAM.

```bash
git clone https://github.com/itisar-345/KnowledgeHubby.git
cd KnowledgeHubby
docker compose up --build
# → http://localhost:8080
```

That starts four containers: ArcadeDB, Ollama, the API and a web server (Caddy) that serves the frontend and proxies the API. The first build takes several minutes, mostly for PyTorch and the embedding model.

- **No configuration needed.** `SECRET_KEY` and `CREDENTIALS_KEY` are generated on first start and kept in the `backend-secrets` volume, so sign-ins and saved connector tokens survive restarts and rebuilds.
- **The first account you create is the server admin.** See [Accounts, roles and spaces](accounts-and-spaces.md).
- **Pick a model at sign-in.** With no LLM installed yet, the sign-in screen offers to download one (4–13 GB) into the `ollama-models` volume.
- **Your data lives in Docker volumes** (`arcadedb-data`, `ollama-models`, `backend-secrets`). `docker compose down` keeps them; `docker compose down -v` deletes everything.
- **Only this machine can connect** by default. Set `KH_BIND=0.0.0.0` to let others on your network in, and `KH_PORT` to use another port than 8080.

**Faster LLM responses.** Ollama in Docker runs on the CPU unless you give it a GPU:
- *NVIDIA (Linux, or Windows with WSL 2):* uncomment the `deploy:` block of the `ollama` service in [`docker-compose.yml`](../docker-compose.yml).
- *macOS (Apple silicon), or an Ollama you already run:* install [Ollama](https://ollama.com) natively, then start with
  `OLLAMA_BASE_URL=http://host.docker.internal:11434 docker compose up --build --scale ollama=0`.

**Folder and git-ADR connectors** can read the `sources/` folder next to `docker-compose.yml` (mounted read-only at `/data/sources`); set `CONNECTOR_SOURCES_DIR` to use another folder. Point connectors at paths under `/data/sources`.

Every optional setting is listed at the top of [`docker-compose.yml`](../docker-compose.yml). Put them in a `.env` file next to it or in your shell environment.

## Run it from source

A single API process, with background work run in-process.

Requires Python 3.11, Node 20.19+ (or 22.12+), Docker (for ArcadeDB) and [Ollama](https://ollama.com), running.

```bash
# 1. ArcadeDB. Use an 8+ character password, the same value as ARCADEDB_PASSWORD in backend/.env.
#    The named volume keeps your data when the container is removed or upgraded.
#    (Without Docker: download the server from https://github.com/ArcadeData/arcadedb/releases; needs Java 21.)
docker run -d --name arcadedb --restart unless-stopped -p 2480:2480 \
  -v arcadedb-data:/home/arcadedb/databases \
  -e JAVA_OPTS="-Darcadedb.server.rootPassword=change-me" arcadedata/arcadedb:26.9.1

# 2. configuration
cd backend
cp .env.example .env
#   required:                    SECRET_KEY         python -c "import secrets; print(secrets.token_urlsafe(48))"
#                                ARCADEDB_PASSWORD
#   for connectors with tokens:  CREDENTIALS_KEY    python -m app.crypto
#   for folder / git connectors: CONNECTOR_ROOTS    e.g. C:\Users\me\Meetings;C:\code

# 3. local LLM: nothing to do. If none of the three models is installed, the app's
#    sign-in screen downloads one for you (or: ollama pull llama3.1:8b)

# 4. backend  (Windows shown; on macOS/Linux: source .venv/bin/activate)
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
# the app runs offline (TRANSFORMERS_OFFLINE=1), so fetch the embedding model once:
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
uvicorn app.main:app --reload --port 8000   # creates the database, schema and migrations on first start

# 5. frontend
cd ../frontend && npm install && npm start
# → http://localhost:4200
```

**The first account you create is the server admin.** Everyone who registers after that is a normal user. See [Accounts, roles and spaces](accounts-and-spaces.md).

The first embedding call loads the sentence-transformers model from the local cache (step 4): the app runs with `TRANSFORMERS_OFFLINE=1`, so it never downloads anything on its own. If you change `LOCAL_EMBED_MODEL`, fetch that model the same way.

### Frontend ↔ backend

The SPA calls the API on its own origin.

- **Development:** `frontend/proxy.conf.cjs` forwards `/auth`, `/admin`, `/spaces`, `/groups`, `/invitations`, `/knowledge`, `/connectors`, `/models` and `/health` to `http://localhost:8000`. A browser page load of a path that is also an Angular route (`/knowledge`, `/connectors`, `/groups`, `/admin/users`) gets the app instead of the API.
- **Every request:** `authInterceptor` adds the JWT and the active space (`X-Space` header). A 401 signs you out. If the server says you're no longer in the active group, the app falls back to your personal space.
- **Production:** serve the built app (`frontend/dist/frontend/browser`) and the API from the same origin, with a reverse proxy routing those prefixes to FastAPI. The backend's CORS settings only allow `localhost:4200` and `localhost:3000`.

Every backend endpoint has a caller in the UI and every UI call has an endpoint; a test enforces this (see [Testing](testing.md)).

## Migrating from the SQLite version

Earlier versions stored data in SQLite (`backend/data/knowledge.db`), optionally mirrored to Neo4j. To copy it into ArcadeDB:

```bash
cd backend
python -m scripts.migrate_sqlite_to_arcadedb --sqlite data/knowledge.db
```

- **Reads only:** the SQLite file is opened read-only, and the target database must be empty.
- **Keeps your data intact:** ids, password hashes (so logins keep working), review state and embeddings carry over.
- **Converts relationships into edges:** relationship rows become `CONTAINS` / `LINK` edges, and cross-links become `RELATED_TO` edges.
- **Skips broken links:** edges whose endpoints no longer exist are skipped and reported. The old storage could leave orphaned relationship rows behind.
- **Checks itself:** it compares row counts at the end and exits non-zero on any mismatch.

Neo4j held only a copy of the SQLite data, so there's nothing to migrate from it. Upgrade steps that apply to every existing database (roles, usernames, encrypted credentials, activity tracking) run automatically at startup; see [`backend/app/migrations.py`](../backend/app/migrations.py).
