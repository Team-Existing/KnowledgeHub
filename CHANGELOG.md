# Changelog

Notable changes to Knowledge Hubs. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Single sign-on** ([docs](docs/sso.md)): sign in with Google, Microsoft or any OpenID Connect provider (code flow with PKCE). Existing accounts connect a provider on the new **Account** page; accounts are never linked by matching email. Organization owners can let everyone with a verified address at their own email domain join automatically (public mail domains excluded).
- **Organizations** ([docs](docs/accounts-and-spaces.md)): the tenant for a hosted service. Each has a shared space, groups inside it, owner / admin / member roles and expiring invite links (only a hash of the token is stored). Leaving or being removed ends the organization's group memberships; deleting it deletes its groups and data. On upgrade, existing accounts and groups move into one organization.
- **Hosted models** ([docs](docs/hosted-models.md)): a server can offer the models of an OpenAI-compatible API (OpenAI, OpenRouter, Groq, vLLM, LM Studio …) next to the local ones, with `LLM_API_BASE_URL`, `LLM_API_KEY` and `LLM_API_MODELS`. Each user chooses; sign-in, the Models page, the provider badge and the privacy page say where text is sent. `OLLAMA_ENABLED=false` runs without local models. Rate limits are retried, and newer OpenAI parameters and servers without JSON schema support are handled.
- **Knowledge health** ([docs](docs/knowledge-health.md)): current items are flagged *may be out of date* on evidence, never on age alone: their source changed, they disappeared from it, a decision they amend was reversed or superseded, a risk's mitigation was dropped or the risk happened anyway, or someone flagged them. Optional review dates mark items *needs review*. A **Health** page lists what needs a look; each item's page can confirm it (clearing the signals, with the next review date), flag it or set a review date. GraphRAG marks such items in its context and the model says when it relies on one.
- One-command local stack: `docker compose up --build` at the repository root runs ArcadeDB, Ollama, the API and the frontend, with secrets generated on first start.
- Documentation split into [`docs/`](docs/), plus `CONTRIBUTING.md`, `SECURITY.md`, a code of conduct, issue and pull request templates.
- Continuous integration: backend tests (with ArcadeDB and Redis), frontend tests and build, and the Docker images.
- A "Source code" link in the app's sidebar, as the AGPL requires for network use.
- A screenshot of every page in `docs/images/`, a [screenshot gallery](docs/screenshots.md), and an animated tour at the top of the README.

- `SETUP_TOKEN`: registering the first account, which becomes the server admin, needs this token. The production stack requires it.
- Sign-in and sign-up throttling (`AUTH_RATE_LIMIT`): repeated failed sign-ins get 429 with `Retry-After`. Counters are shared through Redis across app instances.
- **Sign out** revokes the session on the server (`POST /auth/logout`): every token of the account stops working.
- A Content Security Policy and a `Permissions-Policy` header from the web server.
- `API_DOCS`: `false` hides `/docs`, `/redoc` and `/openapi.json`; the production stack sets it.
- [`deploy/backup.sh`](deploy/backup.sh): a consistent ArcadeDB backup copied to `deploy/backups/`, with old copies pruned. The deployment guide describes restoring.

- `GET /knowledge/items` (filtered, paged items) and `GET /knowledge/items/{id}/context` (what an item's page shows).
- GraphRAG answers whose model names its sources without writing their ids get those sources inferred from the wording, marked "matched".

- [`backend/evals/`](backend/evals/): an end-to-end evaluation of extraction and GraphRAG against a real model, with a fixed corpus, answer key and questions.

### Changed
- **Groups belong to an organization, and group admins only find its members.** Searching for people to invite used to cover every account on the server.
- **An edited source no longer deletes what was accepted from it.** When a source changes and no longer contains an item (an ADR's decision was reworded, so its title changed), an accepted item, or one with a decision history, is kept and flagged *no longer in its source* instead of being deleted with its lineage links and history. Unreviewed items are still replaced.
- **GraphRAG answers are grounded, cited by number, and about 6× faster** (measured with `backend/evals/` on llama3.1:8b, CPU: 15 → 19 of 19 answers correct, 4 → 0 false "not covered" replies, median answer time 78 s → 13 s):
  - The model now sees each item's details (who, why, due, steps), its source's title, and the passage it came from. It used to see titles only, and answered "the context doesn't say who" for owners on record.
  - Context entries are numbered and cited as `[1]`, `[2]`, mapped back to items; small models copied long ids poorly.
  - One fixed reply when nothing in the knowledge base is about the question ("The knowledge base doesn't cover this."), with no sources. A refusal no longer gets sources inferred from the topics it names.
  - The LLM question rewrite (sub-queries + HyDE), LLM rerank and LLM routing are off by default (`GRAPHRAG_QUERY_REWRITE`, `GRAPHRAG_RERANK`, `GRAPHRAG_LLM_ROUTE`): each cost about as long as the answer itself, and retrieval found the right source every time without them.
  - Shorter answers (at most about 120 words, no preamble or closing summary).
- Extraction replies are constrained to a JSON schema (Ollama structured outputs), and a decision is kept in one item with its reason.
- Ollama keeps the model loaded for 30 minutes between calls (`OLLAMA_KEEP_ALIVE`) instead of unloading it after 5.
- **Lists are bounded.** `GET /knowledge` returns a page (default 100) with `totals`, filters items on the server, and leaves out document text unless `include_content=true`; the review queue, decision register, graph and search have limits too. On 3,000 items the Hub's response was 4 MB and, under load, ran ArcadeDB out of memory. The pages show **Load more**.
- **Ingestion no longer waits for the summary.** The LLM summary of a new source is built after the response (about 30 s on a CPU).
- **App instances run several server processes** (`APP_WORKERS`, default 2), and bcrypt runs on a worker thread.
- The Ollama status behind the public model catalog and sign-in is cached for 5 seconds (`OLLAMA_STATUS_CACHE_SECONDS`).
- **Passwords:** at least 8 characters (was 6), not a common password, not the username. Existing passwords keep working.
- Accessibility: one `<h1>` per page, labelled filters, a group role for the graph, underlined links in text, and text colours with at least 4.5:1 contrast in both themes.
- **License: GPL-3.0 → AGPL-3.0.**
- Angular 17 → 21. Building the frontend needs Node 20.19+ or 22.12+; the Docker images use Node 22.
- Backend dependencies upgraded to current releases, including FastAPI 0.142 (Starlette 1.7), Pydantic 2.13, sentence-transformers 6, NumPy 2 and Celery 5.6.
- JWTs are handled by PyJWT instead of python-jose, and passwords by `bcrypt` directly instead of passlib. Existing password hashes and sessions keep working.
- Tokens without an expiry are refused.
- Docker base images: Caddy 2.10, Node 22.

### Fixed
- A write that collided with another on the same ArcadeDB storage page failed outright ("Concurrent modification… Please retry"): a 3,000-item import was lost when a summary was saved meanwhile. Ingest, embedding and summary writes now run again on such a conflict (only the writes; extraction isn't repeated).
- Finding the relationships for a page scanned every edge in the space: about a second for the Hub on 6,000 items. They're now found by walking out from the page's nodes (Hub 2.4 s → 0.2 s, graph 1.3 s → 0.16 s).
- Signing in with an unknown username answered faster than with a known one, which told them apart.
- Source results in search couldn't be opened.
- First run: the onboarding page had no setup-token field, so with `SETUP_TOKEN` set the first account couldn't be created there. It now asks for the token, as the sign-in page does.
- Switching spaces while on the Hub kept showing the previous space's knowledge until a reload (the switch navigated through `/`, which redirects to the Hub itself).
- A group, playbook or other record created a moment ago could be missing from the next list in the clustered stack: reads went to a follower that hadn't applied the write yet. The app now sends its work to the cluster's leader.
- With Redis down, sign-in took up to 15 seconds and sometimes answered 503 "Ollama is not running". The rate limiter and the health probe now give Redis 2 seconds and then leave it alone for 30.
- Members opening `/admin/users` saw the account form with an error; they're now sent to the Hub.
- URL fetching could be redirected to a private address by DNS rebinding. The address is now checked again when connecting, and the connection goes to exactly that address.
- The production frontend build no longer loads its stylesheet through an inline `onload` handler (`inlineCritical` off), which the Content Security Policy would block.
- Reloading a page whose path is also an API path (such as `/knowledge`) made the app show "Failed to load knowledge": the browser reused the cached page for the app's data request. The web server now sends `Vary: Accept`.

### Removed
- Unused dependencies: `openai`, `tiktoken`, `aiofiles`, `python-jose`, `passlib`.
