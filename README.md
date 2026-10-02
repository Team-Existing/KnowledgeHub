# Knowledge Hubs

A **local-first knowledge base** for teams. It turns meeting notes, retros, decision logs, documents, web pages and transcripts into structured knowledge (decisions, risks, lessons, how-tos) that you can review, search, track over time and question in plain English.

- **Runs on your machine.** FastAPI backend, Angular 17 frontend, **ArcadeDB** as the only data store, and local AI: one of three LLMs run by [Ollama](#local-models) plus sentence-transformers embeddings. There are no cloud AI providers.
- **Personal and group spaces.** Everyone has a private space and can create or join groups that share one. A switcher in the nav picks where you're working.
- **Decisions have a history.** A decision can supersede, reverse or amend an older one, and risks can be mitigated or actually happen. The app tracks each item's status and shows how it evolved.
- **Connects only where you point it.** Knowledge can come from folders, git repositories (ADRs), GitHub, Jira and Linear, but only from addresses a user enters, and only when they click **Sync now**.
- **One process for development, a resilient stack for production.** Locally it runs as a single API process. [`deploy/`](deploy/) runs it behind a TLS load balancer, with two app instances, a Celery worker and a three-node ArcadeDB cluster. Each layer has been tested surviving the loss of a component.

## Contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [Production deployment](#production-deployment)
- [Configuration](#configuration)
- [Accounts, roles and spaces](#accounts-roles-and-spaces)
- [Decision tracking](#decision-tracking)
- [Connectors](#connectors)
- [Inactive accounts and groups](#inactive-accounts-and-groups)
- [Local models](#local-models)
- [Architecture](#architecture)
- [GraphRAG pipeline](#graphrag-pipeline)
- [API](#api)
- [Security notes](#security-notes)
- [Tests](#tests)
- [Known limitations](#known-limitations)

---

## What it does

| Page | What you do there |
|---|---|
| **Hub** | Add knowledge: paste text, upload a file (PDF, TXT, MD, DOCX), fetch a URL, or submit a transcript, email thread or Slack export. Browse everything in the space, see the graph, cross-link items, and **Export** / **Import** the space as an OKF (Open Knowledge Format) JSON file |
| **Search** | Full-text search over items and sources, filtered by type, source and tag |
| **Review** | Accept, edit or reject extracted items. Everything starts `pending`; rejected items are never used by GraphRAG |
| **Decisions** | The decision log and risk register, with each item's status and what replaced it |
| **Playbooks** | Repeatable procedures (a release, onboarding), written as steps or built from how-tos and checklists already in the space |
| **Sources** | Connectors that pull in transcripts, notes, ADRs, pull requests and issues |
| **Groups** | Create groups, invite people, answer invitations |
| **GraphRAG** | Ask questions; answers cite the items they used and flag superseded decisions |
| **Models** | Download, switch or remove local LLMs; re-embed the knowledge base |
| **Users** *(server admins)* | Create accounts, change roles, see inactive accounts and groups due for deletion |

**How knowledge is extracted:**

| Source | Extractor | Item types |
|---|---|---|
| Pasted text | Rule-based (keywords) | decision, how-to, lesson, risk, best-practice, checklist |
| Files and URLs | LLM, chunked (~6,000 chars per chunk, up to 8 chunks). Regex fallback for the whole document if no LLM answers, or per chunk if a reply can't be parsed | decision, action-item, how-to, best-practice, lesson, risk, plus checklists (always regex) |
| Transcripts, email, Slack | LLM, with a regex fallback | decision, action-item, risk |
| Git ADRs (connector) | None: each ADR *is* a decision | decision, with its status and lineage |

Each item records which extractor produced it, shown as a badge in the review queue.

**Search** uses ArcadeDB's Lucene indexes. Items match on title, tags and type; sources on title, author, tags and body text. Sources matched only in their body rank below metadata matches and come back with a snippet.

**Cross-linking** (the Hub's **Cross-link** button) connects items from different sources whose titles share enough keywords (Jaccard similarity ≥ 0.12), as `RELATED_TO` edges.

The app checks `GET /health` every minute and shows a banner if the backend or the database can't be reached.

---

## Quick start

For development: a single API process, with background work run in-process. For a multi-instance setup, see [Production deployment](#production-deployment).

Requires Python 3.11, Node 18+, Docker (for ArcadeDB) and [Ollama](https://ollama.com), running.

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
uvicorn app.main:app --reload --port 8000   # creates the database, schema and migrations on first start

# 5. frontend
cd ../frontend && npm install && npm start
# → http://localhost:4200
```

**The first account you create is the server admin.** Everyone who registers after that is a normal user. See [Accounts, roles and spaces](#accounts-roles-and-spaces).

The first embedding call loads the sentence-transformers model, which must already be in the local cache: the app runs with `TRANSFORMERS_OFFLINE=1`, so it never downloads anything on its own.

### Frontend ↔ backend

The SPA calls the API on its own origin.

- **Development:** `frontend/proxy.conf.cjs` forwards `/auth`, `/admin`, `/spaces`, `/groups`, `/invitations`, `/knowledge`, `/connectors`, `/models` and `/health` to `http://localhost:8000`. A browser page load of a path that is also an Angular route (`/knowledge`, `/connectors`, `/groups`, `/admin/users`) gets the app instead of the API.
- **Every request:** `authInterceptor` adds the JWT and the active space (`X-Space` header). A 401 signs you out. If the server says you're no longer in the active group, the app falls back to your personal space.
- **Production:** serve the built app (`frontend/dist/frontend/browser`) and the API from the same origin, with a reverse proxy routing those prefixes to FastAPI. The backend's CORS settings only allow `localhost:4200` and `localhost:3000`.

Every backend endpoint has a caller in the UI and every UI call has an endpoint; a test enforces this (see [Tests](#tests)).

### Migrating from the SQLite version

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

Neo4j held only a copy of the SQLite data, so there's nothing to migrate from it. Upgrade steps that apply to every existing database (roles, usernames, encrypted credentials, activity tracking) run automatically at startup; see [`backend/app/migrations.py`](backend/app/migrations.py).

---

## Production deployment

[`deploy/`](deploy/) runs Knowledge Hubs as several cooperating containers:

```
[ Internet / Clients ]
         │
         ▼
┌────────────────────────┐
│  Load Balancer (TLS)   │   Caddy: HTTPS, serves the frontend, round robin + health checks
└──────────┬─────────────┘
     ┌─────┴────────────────────┐
     ▼                          ▼
┌────────────────────────┐ ┌────────────────────────┐
│  Server Instance 1     │ │  Server Instance 2     │   app1, app2: stateless FastAPI
│  (App Process)         │ │  (App Process)         │
└────┬──────────────┬────┘ └────┬──────────────┬────┘
     │  ┌───────────┼───────────┘              │
     ▼  ▼           └───────────┐              ▼
┌────────────────────────┐ ┌────────────────────────┐
│ Dedicated Background   │ │ ArcadeDB Cluster       │   arcadedb-0/1/2: Raft, any node
│ Worker (Celery/Redis)  │ │ (Dedicated App User)   │   serves; app user = one DB only
└────────────────────────┘ └────────────────────────┘
```

| Service | Role |
|---|---|
| `lb` | [Caddy](deploy/Caddyfile). Terminates TLS (a real certificate for a domain via Let's Encrypt, or its own local CA for `localhost`). Serves the built Angular app. Round-robins API requests across the app instances and checks `/health` every 10 s, routing only to healthy ones. If an instance is down, a request that never reached it is retried on another. Adds security headers. |
| `app1`, `app2` | The API, stateless: any instance can serve any request. They share nothing but ArcadeDB and Redis. |
| `worker` | Celery: runs connector syncs and the inactivity clean-up. A redeploy lets a running sync finish (5-minute grace period). If the worker dies mid-sync, the task is re-queued (`acks_late`) and runs again. |
| `beat` | Celery beat: schedules the clean-up. Run exactly one. |
| `redis` | Task queue, plus the locks every instance shares (one sync per connector, one clean-up at a time). Password-protected, persisted to disk. |
| `arcadedb-0/1/2` | A three-node ArcadeDB cluster (Raft). Any node accepts reads, writes and transactions; it survives one node down. |
| `provision` | One-off job. Waits for the cluster to elect a leader, creates the database and the app's own ArcadeDB user (access to that database only), then applies the schema and migrations once. **The only service that receives the ArcadeDB root password.** |

### Deploy

```bash
cd deploy
cp .env.example .env
# fill in deploy/.env: SITE_ADDRESS, and generate every secret:
#   SECRET_KEY, ARCADEDB_ROOT_PASSWORD, ARCADEDB_APP_PASSWORD, REDIS_PASSWORD:
#       python -c "import secrets; print(secrets.token_urlsafe(32))"
#   CREDENTIALS_KEY:   (in backend/)  python -m app.crypto
docker compose up -d --build
# → https://<SITE_ADDRESS>/      (the first account you create is the server admin)
```

- **TLS:** with `SITE_ADDRESS=localhost`, browsers warn until you trust Caddy's local CA. With a real domain, ports 80 and 443 must be reachable for Let's Encrypt.
- **Ollama** runs on the Docker host (`OLLAMA_BASE_URL`, default `http://host.docker.internal:11434`).
- **Folder and git-ADR connectors** read `CONNECTOR_SOURCES_DIR` (default `deploy/sources`), mounted read-only at `/data/sources` in the app and worker containers. Point folder connectors at paths under `/data/sources`.
- **The backend image** bakes in CPU-only PyTorch and the embedding model (about 2.5 GB), so nothing is downloaded at runtime.

### What was verified

Run against this stack, through the load balancer:

| Scenario | Result |
|---|---|
| 20 requests | split 10 / 10 across `app1` and `app2` |
| `app1` stopped | every request still succeeded |
| ArcadeDB leader stopped | another node became leader within seconds; `/health` reported `degraded` (2 of 3 nodes); reads **and writes** kept working; the node rejoined when restarted |
| Worker killed mid-sync | the connector showed "interrupted"; Celery re-queued the task; the sync completed without duplicates (unchanged documents skipped) |
| Sync started from the UI | queued by the API, run by the worker, never by an app instance |
| Clean-up | scheduled by beat; locked so concurrent runs can't overlap |
| Database access | the app connects as `kh_app`; the root password reaches only the ArcadeDB nodes and `provision` |

| Redis stopped | `/health` `degraded` in under 2 s; reads, ingest, search and the connectors list kept working; starting a sync answered 503 "background queue unavailable"; syncs queued again once Redis was back |
| Rolling restart (`app1`, then `app2`) | one instance was serving at all times |

Two scenarios found real bugs, both now fixed and covered by tests. They were the same mistake twice: a slow dependency made `/health` slower than the load balancer's 5-second check, so the balancer took every healthy instance out of rotation and turned a partial outage into a full one.

The first run of the "leader stopped" scenario found the first one. `/health` probed the database nodes one by one, so a dead node made each check take 15 seconds. The load balancer then marked **both** healthy app instances as down. Probes now run in parallel with a 2-second limit, and the database client gives up connecting to a dead node after 3 seconds. The second: with Redis stopped, the health check's Redis ping had no timeout and hung. Redis calls now time out after 2 seconds.

### Operating it

- **More app instances:** copy `app2` in `docker-compose.yml` and add it to `APP_UPSTREAMS`. More sync throughput: raise the worker's `--concurrency`, or run more `worker` replicas. Keep **one** `beat`.
- **Logs:** `docker compose logs -f app1 worker`. Health: `curl -k https://<site>/health` shows each node, the queue and the background mode.
- **Backups:** each ArcadeDB node keeps its data in its own volumes (`arcadedb-N-databases`, `-config`, `-replication`, `-backups`). Back up at least one node's `databases` volume, plus `redis-data` for queued work.
- **Upgrades without downtime:** `docker compose build provision lb`, then `docker compose run --rm provision` (idempotent: it re-applies the app user and runs any new migrations). Then restart the instances one at a time, so one is always serving: `docker compose up -d --no-deps app1`, wait for it to report healthy, then `docker compose up -d --no-deps app2 worker`. A plain `docker compose up -d --build` also works, but restarts both instances at once.
- **Rotating secrets:** change the value in `deploy/.env` and run `docker compose up -d`. For `CREDENTIALS_KEY`, use `<new>,<old>` for one restart (see [Security notes](#security-notes)). For `ARCADEDB_APP_PASSWORD`, `provision` re-applies it before the app instances restart.

---

## Configuration

All settings go in `backend/.env` ([`.env.example`](backend/.env.example) has every one with comments). For the production stack they go in `deploy/.env` ([`deploy/.env.example`](deploy/.env.example)) instead; Compose passes each container only the ones it needs.

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

---

## Accounts, roles and spaces

**Every account is a normal user.** On top of that there are two separate kinds of "admin":

| | Who | What they can do |
|---|---|---|
| **Server admin** | The first account created on the server, plus anyone a server admin promotes | Create accounts and change roles (**Users** page), install or remove models on the shared Ollama, use folder and git-ADR connectors (they read the server's disk), see and run the inactivity clean-up |
| **Group admin** | Whoever creates a group | Invite and remove members, rename or delete that group |

Set `ALLOW_REGISTRATION=false` to make server admins create every account. Usernames are unique ignoring case (`Alice` and `alice` are the same name); new names use letters, digits, `.`, `_` and `-`, 3 to 60 characters.

### Spaces

- **Your personal space** is private: only you can see it.
- **Each group you join has its own space.** Everyone in it can view and work on everything there: sources, knowledge items, reviews, decisions and their history, playbooks, connectors, search and GraphRAG. Item history records who did what.
- **The switcher at the top** picks the active space, and a banner shows when you're working in a group.
- **Share a source into a group:** on an item's page, **Share this source to…** copies its source, with all its knowledge items, review state and statuses, into another space you belong to. Sharing again updates the copy rather than duplicating it.

### Groups

- **Creating:** anyone can create a group on the **Groups** page and becomes its admin.
- **Inviting:** the group admin searches users by name and invites them. The invited user sees a badge on **Groups** and accepts or declines. Until they accept, they have no access.
- **Managing (group admin):** rename the group, remove members or cancel invitations (what a removed member added stays in the group), or delete the group, which deletes its space for everyone.
- **Leaving:** members can leave. The group admin can't; they delete the group instead.

**How it works:** the frontend sends the active space in an `X-Space` header. The backend ([`backend/app/spaces.py`](backend/app/spaces.py)) checks you're an active member, then scopes every query to that space. A request for a group you're not in, or for another user's personal space, gets a 403.

---

## Decision tracking

Every **decision** and **risk** has a lifecycle status:

| Type | Statuses |
|---|---|
| Decision | `active` (default), `proposed`, `superseded`, `reversed`, `deprecated`, `rejected` |
| Risk | `open` (default), `mitigated`, `materialized`, `closed` |

Items are linked newer → older:

| Link | Between | Effect |
|---|---|---|
| supersedes | decision → decision | the older decision becomes `superseded` |
| reverses | decision → decision | the older decision becomes `reversed` |
| amends | decision → decision | part of the history; no status change |
| realizes | any → risk | the risk happened: `materialized` |
| mitigates | any → risk | `mitigated` |
| learned from | lesson → any | the lesson came out of that item |

- **Links set the status.** Delete the link (or the newer item) and the older one goes back automatically.
- **You can also declare a status by hand,** e.g. a decision dropped without a replacement, or a risk that's closed. A link still takes priority over a declared status.
- **Invalid links are refused:** an item linked to itself, a pair linked twice, the wrong types (a lesson can't supersede a decision), or a circular history.
- **History.** Reviews, edits, links and status changes are logged per item, with who did them.
- **Suggestions.** Each item's page proposes probable links from similar items in other sources, e.g. "this newer decision probably supersedes that 2023 one". One click accepts.
- **Everywhere else:** status badges appear on the Hub, Search, Review and GraphRAG sources. GraphRAG presents the replacement as the current decision.

Where to see it: the **History & lineage** panel on each item's page, and the **Decisions** page.

---

## Connectors

The **Sources** page connects knowledge where it already lives. **The app never contacts a service on its own:**

- **Only the addresses you give.** No connector has a built-in web address. Each contacts only what a user typed into it, and refuses anything else, including redirects and links inside API responses.
- **Only when you click Sync now.** Nothing syncs on a schedule or at startup.
- **Credentials are encrypted.** Tokens are stored encrypted, decrypted only during a sync, and never sent back to the browser.

| Source | What you provide | What's ingested |
|---|---|---|
| Local folder *(server admins)* | A folder path inside `CONNECTOR_ROOTS` | Meeting captions (`.vtt`/`.srt` from Zoom, Teams or Meet, turned into "Speaker: text"), plus `.md`, `.txt`, `.pdf`, `.docx` |
| Git ADRs *(server admins)* | A local clone's path inside `CONNECTOR_ROOTS` | Each Architecture Decision Record becomes a decision, with no LLM. "Superseded by" / "Supersedes" / "Amends" lines become lineage links. Dates and authors come from the ADR, or the commit that added it |
| GitHub | The repository link, e.g. `https://github.com/acme/api` or a GitHub Enterprise link; a token for private repos | Merged pull requests and their discussion |
| Jira | Your Jira site, e.g. `https://acme.atlassian.net`; an API token (plus email for Cloud); a JQL query | Issues and their comments |
| Linear | Linear's API address, `https://api.linear.app/graphql`; a personal API key | Issues and their comments |

A github.com link is read through GitHub's API host, `api.github.com`; an Enterprise link through `<your host>/api/v3`.

- **Syncing is safe to repeat.** Each source document becomes one artifact with a fixed id. Unchanged documents (same content hash) are skipped without calling the LLM. A changed document updates its artifact in place, so items whose text didn't change keep their review state and lineage.
- **One bad document doesn't stop the rest.** Failures are listed on the connector's card.
- **Where syncs run.** In production, the API only queues the sync (**Queued…**) and the Celery worker runs it. If the worker dies mid-sync, the card shows **Interrupted** until the task is re-queued and finishes. In development they run inside the API process.
- **Removing a connector** keeps what it imported, unless you choose to delete that too.
- **In a group,** connectors belong to the group: every member sees them and can sync them, using the stored credentials. The card shows who added each one.

---

## Inactive accounts and groups

**Anything unused for a year is deleted, with its data.** The period is `RETENTION_INACTIVE_DAYS` (default 365).

| What | Counts as active | Deleted when idle |
|---|---|---|
| Account | signing in, or any use of the app | the account, its personal space and everything in it, its group memberships and invitations, and the GraphRAG questions it asked in groups |
| Group | any member working in the group's space | the group, its memberships, and everything in its space |

- **What a deleted user added to a group stays in the group.** If they were a group's admin, the longest-standing remaining member becomes admin. A group with no other members goes with them.
- **Safety rails:**
  - The last server admin is never deleted.
  - Spaces in the middle of a connector sync are skipped until the next run.
  - On upgrade, existing accounts and groups start their clock then, so nothing is deleted for inactivity from before tracking existed. This happens once.
- **When it runs.** Every `RETENTION_CHECK_HOURS` (default 24). In production, Celery beat schedules it on the worker; in development the API runs it at startup and then on that interval. Runs are locked, so the scheduled run and an admin's **Delete these now** never overlap. Set `RETENTION_ENABLED=false` to switch it off; admins still see what it *would* delete.
- **Visibility.** On the **Users** page, server admins see each account's last activity and expiry date, what's due now, what's due within 30 days, and the last run, and can delete what's due immediately. The **Groups** page shows each group's expiry date.

Activity is written at most once an hour per account or group, outside the request's transaction, so it doesn't slow down normal use.

---

## Local models

Knowledge Hubs supports exactly three LLMs, all downloaded and run locally by Ollama:

| Model | Ollama tag | Download | RAM |
|---|---|---|---|
| Llama 3.1 8B | `llama3.1:8b` | 4.7 GB | 8 GB+ |
| Mistral 7B | `mistral:7b` | 4.1 GB | 8 GB+ |
| GPT-OSS 20B | `gpt-oss:20b` | 13 GB | 16 GB+ |

The list lives in [`backend/app/services/llm_catalog.py`](backend/app/services/llm_catalog.py). Other models Ollama may have are ignored.

- **Signing in needs one of the three.** The sign-in and onboarding screens first check what's installed:
  - **None installed:** you get a download screen. Pick a model; the one that suits your RAM is marked recommended. Sign-in only appears once the download finishes.
  - **One already installed:** you go straight to sign-in.
  - **Ollama not running:** the screen says so, with a Retry button.

  The server enforces the same rule: `POST /auth/token` returns 412 if no model is installed and 503 if Ollama is down.
- **Each user picks their model** on the **Models** page.
  - Your choice is saved on your account, so it applies to your future sessions.
  - It's used for every LLM call you make: extraction, summaries, GraphRAG, and connector syncs you start.
  - If your model disappears (removed on the Models page or with `ollama rm`), you're moved to another installed one.
- **Server admins download and remove models,** because every account on the machine shares the same Ollama. At least one model always stays installed.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Angular 17 SPA                                              │
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

### Data model

The schema lives in [`backend/app/schema.py`](backend/app/schema.py) and is applied idempotently at startup.

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

---

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

---

## API

All endpoints except `/auth/register`, `/auth/token`, `GET /health`, `GET /models/catalog` and `POST /models/bootstrap-install` need `Authorization: Bearer <token>`. Data endpoints act in the space named by the `X-Space` header (a group id, or your own user id); without it they use your personal space. Interactive docs are at `http://localhost:8000/docs`.

| Group | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/token` (form fields `username`, `password`; token valid for 8 h), `GET /auth/me` (role and permissions) |
| Admin *(server admins)* | `GET /admin/users` (with last activity and expiry), `POST /admin/users` (create with a role), `PATCH /admin/users/{id}` (change role), `GET /admin/retention` (what's due), `POST /admin/retention/run` |
| Spaces & groups | `GET /spaces`; `POST/GET /groups`, `PATCH/DELETE /groups/{id}`, `GET /groups/{id}/members`, `GET /groups/{id}/candidates?q=` (search users), `POST /groups/{id}/invitations`, `DELETE /groups/{id}/members/{user_id}`, `POST /groups/{id}/leave`; `GET /invitations`, `POST /invitations/{group_id}/accept`, `/decline` |
| Ingest | `POST /knowledge/artifacts` (text), `/artifacts/upload` (multipart), `/artifacts/url`, `/artifacts/transcript` |
| Artifacts | `PUT /knowledge/artifacts/{id}` (a content change re-extracts items), `DELETE /knowledge/artifacts/{id}`, `POST /knowledge/artifacts/{id}/share` (copy into another space) |
| Items | `GET/PUT/DELETE /knowledge/items/{id}`, `GET /knowledge/{ref}` (by id, id suffix or title) |
| Browse | `GET /knowledge` (everything in the space), `GET /knowledge/graph` |
| Review | `GET /knowledge/review`, `PATCH /knowledge/review/{id}` |
| Search | `GET /knowledge/search?q=&type=&source_type=&tag=&limit=` |
| Links | `POST /knowledge/link`, `GET /knowledge/links` |
| Lineage | `GET /knowledge/lineage/kinds`, `GET/POST /knowledge/items/{id}/lineage`, `DELETE /knowledge/items/{id}/lineage/{other}`, `GET …/lineage/suggestions`, `PUT /knowledge/items/{id}/status`, `GET /knowledge/register` |
| Playbooks | `GET /knowledge/playbooks`, `POST /knowledge/playbooks` (same title replaces), `DELETE /knowledge/playbooks/{id}` |
| OKF | `GET /knowledge/okf/export`, `POST /knowledge/okf/import` |
| GraphRAG | `POST /knowledge/graphrag/query`, `POST /knowledge/reembed` |
| Connectors | `GET /connectors/kinds`, `GET/POST /connectors`, `PATCH/DELETE /connectors/{id}`, `POST /connectors/{id}/sync`, `GET /connectors/{id}/documents` |
| Models (public) | `GET /models/catalog` (which of the three are installed), `POST /models/bootstrap-install` (first download; only while none is installed) |
| Models | `GET /models/status`, `/models/local`, `/models/system-info`; `POST /models/set-default` (switch your model); `POST /models/install` (streamed progress), `/models/remove` *(server admins)* |
| Health | `GET /health`: `status` (`healthy` / `degraded`), `arcadedb_nodes` (ready / configured), `background` (`worker` / `in-process`), `queue` |

---

## Security notes

**Accounts**
- **Passwords and tokens.** Passwords are hashed with bcrypt. Tokens are HS256 JWTs signed with `SECRET_KEY` and expire after 8 hours. The frontend sends the token only to its own API.
- **Signing key.** The server refuses to start if `SECRET_KEY` is missing, shorter than 32 characters, or one of the placeholders this repo has ever shipped. Anyone who knows the key could sign in as any account.
- **Roles.** [`backend/app/rbac.py`](backend/app/rbac.py) maps roles to permissions, and each route declares the permission it needs. Roles are read from the database on every request, so a change applies to existing sessions at once. An unknown role grants nothing, and the last server admin can't be demoted.
  - *Upgrading:* databases from before roles existed had every account as admin. On first start, the earliest account stays admin and every other admin becomes a member (logged once).
- **Usernames are unique, ignoring case.** `Alice`, `alice` and ` ALICE ` are the same name, and sign-in isn't case-sensitive; the name keeps the casing it was created with. New names are limited to ASCII letters, digits, `.`, `_` and `-`, so look-alike characters (a Cyrillic `а` for a Latin `a`) can't create a second account that reads the same in member lists and invitations. A UNIQUE index on the normalised name (`User.username_key`, see [`backend/app/usernames.py`](backend/app/usernames.py)) enforces it in the database too.
  - *Upgrading:* existing accounts that differ only in case keep working with their exact name and are listed in a startup warning.

**Data isolation**
- **Spaces.** Every query is scoped to the active space, and the backend checks membership before using a group's space. Artifact and item ids are hashes that include the space id, so the same document in two spaces gives separate nodes, and edges are only ever created between nodes of the same space. The test suite checks all of this.
- **Query safety.** All values are passed to ArcadeDB as bound parameters. Full-text queries are rebuilt from word characters only, so user input can't inject Lucene operators.
- **Rendering.** GraphRAG answers are HTML-escaped before citation badges are inserted.

**Infrastructure (production stack)**
- **A dedicated database user.** The app connects as a user that can read, write and change the schema in its own database, and can't create databases or see any other. Only the one-off `provision` job and the ArcadeDB nodes ever receive the root password; this was checked against the stack's actual configuration.
- **TLS everywhere outside.** Caddy terminates HTTPS, redirects HTTP, and adds HSTS, `X-Content-Type-Options`, `X-Frame-Options: DENY` and `Referrer-Policy`. Internal traffic stays on the Compose network; only the load balancer publishes ports.
- **Secrets stay out of images.** `.dockerignore` files exclude every `.env`, and each container gets only the variables `docker-compose.yml` lists for it. Redis requires a password.

**Connectors and credentials**
- **Stored credentials.** Connector tokens and API keys are encrypted with AES-256-GCM before they reach ArcadeDB ([`backend/app/crypto.py`](backend/app/crypto.py)). Each value is bound to its connector and field, so ciphertext copied elsewhere won't decrypt. They're decrypted only in memory during a sync and never returned by the API (shown as `********`). Without `CREDENTIALS_KEY` they can't be saved at all. Plaintext values left by older versions are encrypted at startup; until then they're refused.
  - *Rotating the key:* set `CREDENTIALS_KEY=<new>,<old>`, restart (everything is re-encrypted with the new key), then drop `<old>`.
- **Outbound connections.** Each sync's HTTP client is locked to the hosts the connector's own settings name, so redirects and URLs inside API responses that point anywhere else are refused before a request, or a token, leaves the server.
- **Folder connectors.** Disabled unless `CONNECTOR_ROOTS` is set. Paths are checked against it when a connector is saved and again at every sync. Directory symlinks aren't followed, and file symlinks that resolve outside the folder are skipped. `git` runs with the repository's `fsmonitor`, hooks and pager overridden and system config ignored, so a scanned repository can't make the server run programs.

**Input**
- **URL fetching.** Only `http(s)` is allowed. Hosts that resolve to private, loopback, link-local (including cloud metadata) or reserved addresses are rejected, and every redirect hop is checked again.
- **Uploads.** Oversized bodies get a 413 before they're buffered. PDF and DOCX are parsed from Starlette's spooled temp file rather than read into memory. Legacy `.doc` is rejected; save it as `.docx`.

**Retention.** Inactive accounts and groups are deleted permanently (see [Inactive accounts and groups](#inactive-accounts-and-groups)). Back up the `arcadedb-data` volume if you need to recover from that.

---

## Tests

The backend tests run against a real ArcadeDB. They create a throwaway database (`kh_test_…`) and drop it afterwards, so they never touch your data. The LLM and embeddings are faked unless a live test opts in. If ArcadeDB isn't reachable, every database test is skipped and the reason is shown.

```bash
cd backend && pip install -r requirements-dev.txt

# bash
KH_TEST_ARCADEDB_PASSWORD=<root password> pytest
# PowerShell
$env:KH_TEST_ARCADEDB_PASSWORD="<root password>"; pytest
#   optional: KH_TEST_ARCADEDB_URL (default http://localhost:2480), KH_TEST_ARCADEDB_USER (default root)

# frontend: Karma + Jasmine (needs Chrome)
cd frontend && npm test -- --watch=false --browsers=ChromeHeadless
```

**Backend coverage** (about 200 tests):
- **Ingestion:** every path, including LLM fallbacks, file formats, limits, URL safety and transactional rollback.
- **Isolation:** across all endpoints, including vector search among other spaces' data and edges never crossing spaces.
- **GraphRAG:** the pipeline with a mocked LLM, including citations and superseded decisions.
- **Decision tracking:** statuses, link rules, history, suggestions and the decision register.
- **Groups and spaces:** creating, searching and inviting, accepting and declining, access before and after joining, shared work, history attribution, sharing a source into a group, removal, leaving, deletion, and that no one can enter a space that isn't theirs.
- **Accounts:** roles and permissions, the first-admin rule and its upgrade migration, signing-key enforcement, and case-insensitive unique usernames.
- **Credentials and connectors:**
  - credential encryption: record binding, tampering, rotation, and migrating legacy plaintext
  - `CONNECTOR_ROOTS`, symlink escapes, and a hostile git config
  - folder and git ADRs against real files and a real `git` repository
  - GitHub, Jira and Linear as offline contract tests with mocked HTTP: pagination, 401s, GraphQL errors, and refusing hosts the user didn't provide
- **Inactivity retention:** activity tracking, deleting idle accounts (personal data gone, group contributions kept), admin hand-over, deleting idle groups, the last-admin rule, the one-time clock start on upgrade, and that recent activity is never deleted.
- **Playbooks:** stable per-space ids, replace by title, delete, privacy.

**Frontend ↔ backend contract** ([`tests/test_frontend_contract.py`](backend/tests/test_frontend_contract.py), no database needed): reads the Angular sources and FastAPI's route table, and fails if any backend endpoint has no caller in the UI or any UI call has no matching endpoint. A feature therefore can't ship with only one half. Endpoints that are deliberately API-only must be listed in `NOT_FOR_THE_UI` with a reason; currently there are none.

**Scaling pieces** ([`tests/test_scaling.py`](backend/tests/test_scaling.py)):
- **Multi-node database client:** fails over on refused connections and "not the leader"; never retries a timed-out write elsewhere; keeps a transaction on its node; explains a missing provisioning step.
- **Health probes:** run in parallel, each with a short timeout.
- **Locks:** in memory, and in Redis, including expiry.
- **Worker:** queuing a sync in worker mode, the real Celery task functions against the database, and refusing duplicate runs.

The Redis tests need a Redis server: set `KH_TEST_REDIS_URL` (e.g. `redis://localhost:6379/15`).

**Live integration tests** ([`tests/test_live_integrations.py`](backend/tests/test_live_integrations.py), marker `live`) run the GitHub, Jira, Linear and Ollama paths end to end against the real services: save the connector with encrypted credentials, sync, check what was stored. Each runs only when its variables are set; otherwise it's skipped and says which variables to set:

```bash
KH_LIVE_GITHUB_REPO_URL=https://github.com/owner/name  [KH_LIVE_GITHUB_TOKEN=...]   # public repos need no token
KH_LIVE_JIRA_URL=https://acme.atlassian.net KH_LIVE_JIRA_TOKEN=... KH_LIVE_JIRA_JQL="project = ARCH" [KH_LIVE_JIRA_EMAIL=...]
KH_LIVE_LINEAR_API_URL=https://api.linear.app/graphql KH_LIVE_LINEAR_API_KEY=lin_api_... [KH_LIVE_LINEAR_TEAM=ENG]
KH_LIVE_OLLAMA=1                                                                   # real LLM extraction; slow on CPU
pytest -m live
```

Use read-only credentials scoped to test data. The offline contract tests stay alongside them: they cover error paths a live service can't be made to produce on demand, and keep the suite meaningful without credentials.

**Frontend coverage:** the auth interceptor and error handling (21 tests).

---

## Known limitations

**Models and search**
- **One LLM, one embedding model.** Only Ollama and local sentence-transformers are implemented.
- **Models are per machine, choices are per user.** Everyone on one server shares the same Ollama, so removing a model affects every account. Anyone using it is moved to another installed model.
- **Full-text search doesn't fold accents.** The default analyzer means `cafe` won't match "Café".
- **Search slows on very broad queries.** On 50,000 items a precise query takes about 75 ms, but a one-word query matching 30,000+ items takes about 300 ms, mostly spent counting the total.
- **Extracted text isn't size-capped.** Uploads and URLs are capped by `MAX_UPLOAD_MB`, but the text extracted from them isn't (pasted text is capped at 100,000 characters). LLM extraction reads at most 8 chunks.

**Connectors**
- **No scheduled syncs.** By design, a connector only syncs when someone clicks **Sync now**.
- **A crashed worker's sync waits for its timeout.** A worker killed outright (not stopped) leaves its sync queued until `CELERY_VISIBILITY_TIMEOUT` (default 2 h) passes, then it runs again. A normal stop or redeploy lets the sync finish. In development, restarting the API mid-sync marks it "interrupted"; sync again.
- **Deleted source files stay.** Removing a file or closing an issue at the source doesn't remove what was already imported.
- **Group connectors share one credential.** Every member of a group can sync a group connector with the token whoever added it saved. Use a token scoped to what the group should see.
- **Per-sync caps.** GitHub reads at most 10 pages of closed pull requests per sync; each connector has a `max` setting for items per sync.

**Data**
- **Deletion is permanent.** Inactivity retention, group deletion and removing a connector's data can't be undone; back up the `arcadedb-data` volume.
- **Shared copies drop lineage links.** Sharing a source into another space copies its items, statuses and review state, but not links to items outside it.
- **Development connects to ArcadeDB as `root`.** The single-process setup uses `ARCADEDB_USER=root` for simplicity. The production stack uses a dedicated app user.

**Production stack**
- **The cluster needs a majority.** Three nodes survive one being down. With two down there's no leader, and writes stop until a node returns.
- **One Ollama for everything.** Every app instance and the worker call the same Ollama on the Docker host, so LLM throughput doesn't grow with more instances.
- **Exactly one `beat`.** Two would schedule the clean-up twice. It's harmless, because runs are locked, but wasteful.
- **No horizontal Redis.** Redis is a single container. If it's down, starting a sync returns "background queue unavailable" (503), and the Sources page can't show live sync state. Everything else (reading, ingesting, search, GraphRAG) keeps working, and `/health` reports `degraded`. This was tested against the stack.

**Performance and network**
- **Writes are one HTTP round trip per statement.** An ingest of N items makes roughly 3N calls to ArcadeDB. That's fine on the same machine, but slower if ArcadeDB is remote.
- **URL fetching is exposed to DNS rebinding.** Addresses are checked, then httpx resolves the host again when connecting, so a hostile DNS server can return a different address the second time.
