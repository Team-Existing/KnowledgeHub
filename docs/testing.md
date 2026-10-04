# Testing

[CI](../.github/workflows/ci.yml) runs the backend suite (against ArcadeDB and Redis service containers), the frontend tests and build, and builds the Docker images on every push and pull request.

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

**Backend coverage** (about 220 tests):
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

**Frontend ↔ backend contract** ([`tests/test_frontend_contract.py`](../backend/tests/test_frontend_contract.py), no database needed): reads the Angular sources and FastAPI's route table, and fails if any backend endpoint has no caller in the UI or any UI call has no matching endpoint. A feature therefore can't ship with only one half. Endpoints that are deliberately API-only must be listed in `NOT_FOR_THE_UI` with a reason; currently there are none.

**Scaling pieces** ([`tests/test_scaling.py`](../backend/tests/test_scaling.py)):
- **Multi-node database client:** fails over on refused connections and "not the leader"; never retries a timed-out write elsewhere; keeps a transaction on its node; explains a missing provisioning step.
- **Health probes:** run in parallel, each with a short timeout.
- **Locks:** in memory, and in Redis, including expiry.
- **Worker:** queuing a sync in worker mode, the real Celery task functions against the database, and refusing duplicate runs.

The Redis tests need a Redis server: set `KH_TEST_REDIS_URL` (e.g. `redis://localhost:6379/15`).

**Live integration tests** ([`tests/test_live_integrations.py`](../backend/tests/test_live_integrations.py), marker `live`) run the GitHub, Jira, Linear and Ollama paths end to end against the real services: save the connector with encrypted credentials, sync, check what was stored. Each runs only when its variables are set; otherwise it's skipped and says which variables to set:

```bash
KH_LIVE_GITHUB_REPO_URL=https://github.com/owner/name  [KH_LIVE_GITHUB_TOKEN=...]   # public repos need no token
KH_LIVE_JIRA_URL=https://acme.atlassian.net KH_LIVE_JIRA_TOKEN=... KH_LIVE_JIRA_JQL="project = ARCH" [KH_LIVE_JIRA_EMAIL=...]
KH_LIVE_LINEAR_API_URL=https://api.linear.app/graphql KH_LIVE_LINEAR_API_KEY=lin_api_... [KH_LIVE_LINEAR_TEAM=ENG]
KH_LIVE_OLLAMA=1                                                                   # real LLM extraction; slow on CPU
pytest -m live
```

Use read-only credentials scoped to test data. The offline contract tests stay alongside them: they cover error paths a live service can't be made to produce on demand, and keep the suite meaningful without credentials.

**Frontend coverage:** the auth interceptor and error handling (22 tests).
