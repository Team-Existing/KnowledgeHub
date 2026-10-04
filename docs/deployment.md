# Production deployment

[`deploy/`](../deploy/) runs Knowledge Hubs as several cooperating containers:

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
| `lb` | [Caddy](../deploy/Caddyfile). Terminates TLS (a real certificate for a domain via Let's Encrypt, or its own local CA for `localhost`). Serves the built Angular app. Round-robins API requests across the app instances and checks `/health` every 10 s, routing only to healthy ones. If an instance is down, a request that never reached it is retried on another. Adds security headers. |
| `app1`, `app2` | The API, stateless: any instance can serve any request. They share nothing but ArcadeDB and Redis. |
| `worker` | Celery: runs connector syncs and the inactivity clean-up. A redeploy lets a running sync finish (5-minute grace period). If the worker dies mid-sync, the task is re-queued (`acks_late`) and runs again. |
| `beat` | Celery beat: schedules the clean-up. Run exactly one. |
| `redis` | Task queue, plus the locks every instance shares (one sync per connector, one clean-up at a time). Password-protected, persisted to disk. |
| `arcadedb-0/1/2` | A three-node ArcadeDB cluster (Raft). Any node accepts reads, writes and transactions; it survives one node down. |
| `provision` | One-off job. Waits for the cluster to elect a leader, creates the database and the app's own ArcadeDB user (access to that database only), then applies the schema and migrations once. **The only service that receives the ArcadeDB root password.** |

## Deploy

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

## What was verified

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

## Operating it

- **More app instances:** copy `app2` in `docker-compose.yml` and add it to `APP_UPSTREAMS`. More sync throughput: raise the worker's `--concurrency`, or run more `worker` replicas. Keep **one** `beat`.
- **Logs:** `docker compose logs -f app1 worker`. Health: `curl -k https://<site>/health` shows each node, the queue and the background mode.
- **Backups:** each ArcadeDB node keeps its data in its own volumes (`arcadedb-N-databases`, `-config`, `-replication`, `-backups`). Back up at least one node's `databases` volume, plus `redis-data` for queued work.
- **Upgrades without downtime:** `docker compose build provision lb`, then `docker compose run --rm provision` (idempotent: it re-applies the app user and runs any new migrations). Then restart the instances one at a time, so one is always serving: `docker compose up -d --no-deps app1`, wait for it to report healthy, then `docker compose up -d --no-deps app2 worker`. A plain `docker compose up -d --build` also works, but restarts both instances at once.
- **Rotating secrets:** change the value in `deploy/.env` and run `docker compose up -d`. For `CREDENTIALS_KEY`, use `<new>,<old>` for one restart (see [Security notes](security.md)). For `ARCADEDB_APP_PASSWORD`, `provision` re-applies it before the app instances restart.
