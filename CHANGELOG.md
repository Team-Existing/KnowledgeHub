# Changelog

Notable changes to Knowledge Hubs. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
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

### Changed
- **License: GPL-3.0 → AGPL-3.0.**
- Angular 17 → 21. Building the frontend needs Node 20.19+ or 22.12+; the Docker images use Node 22.
- Backend dependencies upgraded to current releases, including FastAPI 0.142 (Starlette 1.7), Pydantic 2.13, sentence-transformers 6, NumPy 2 and Celery 5.6.
- JWTs are handled by PyJWT instead of python-jose, and passwords by `bcrypt` directly instead of passlib. Existing password hashes and sessions keep working.
- Tokens without an expiry are refused.
- Docker base images: Caddy 2.10, Node 22.

### Fixed
- URL fetching could be redirected to a private address by DNS rebinding. The address is now checked again when connecting, and the connection goes to exactly that address.
- The production frontend build no longer loads its stylesheet through an inline `onload` handler (`inlineCritical` off), which the Content Security Policy would block.
- Reloading a page whose path is also an API path (such as `/knowledge`) made the app show "Failed to load knowledge": the browser reused the cached page for the app's data request. The web server now sends `Vary: Accept`.

### Removed
- Unused dependencies: `openai`, `tiktoken`, `aiofiles`, `python-jose`, `passlib`.
