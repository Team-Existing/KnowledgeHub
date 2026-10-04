# Changelog

Notable changes to Knowledge Hubs. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- One-command local stack: `docker compose up --build` at the repository root runs ArcadeDB, Ollama, the API and the frontend, with secrets generated on first start.
- Documentation split into [`docs/`](docs/), plus `CONTRIBUTING.md`, `SECURITY.md`, a code of conduct, issue and pull request templates.
- Continuous integration: backend tests (with ArcadeDB and Redis), frontend tests and build, and the Docker images.
- A "Source code" link in the app's sidebar, as the AGPL requires for network use.
- A screenshot of every page in `docs/images/`, a [screenshot gallery](docs/screenshots.md), and an animated tour at the top of the README.

### Changed
- **License: GPL-3.0 → AGPL-3.0.**
- Angular 17 → 21. Building the frontend needs Node 20.19+ or 22.12+; the Docker images use Node 22.
- Backend dependencies upgraded to current releases, including FastAPI 0.142 (Starlette 1.7), Pydantic 2.13, sentence-transformers 6, NumPy 2 and Celery 5.6.
- JWTs are handled by PyJWT instead of python-jose, and passwords by `bcrypt` directly instead of passlib. Existing password hashes and sessions keep working.
- Tokens without an expiry are refused.
- Docker base images: Caddy 2.10, Node 22.

### Fixed
- Reloading a page whose path is also an API path (such as `/knowledge`) made the app show "Failed to load knowledge": the browser reused the cached page for the app's data request. The web server now sends `Vary: Accept`.

### Removed
- Unused dependencies: `openai`, `tiktoken`, `aiofiles`, `python-jose`, `passlib`.
