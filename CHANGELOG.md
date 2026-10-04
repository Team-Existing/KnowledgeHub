# Changelog

Notable changes to Knowledge Hubs. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- One-command local stack: `docker compose up --build` at the repository root runs ArcadeDB, Ollama, the API and the frontend, with secrets generated on first start.
- Documentation split into [`docs/`](docs/), plus `CONTRIBUTING.md`, `SECURITY.md`, a code of conduct, issue and pull request templates.
- Continuous integration: backend tests (with ArcadeDB and Redis), frontend tests and build, and the Docker images.
- A "Source code" link in the app's sidebar, as the AGPL requires for network use.

### Changed
- **License: GPL-3.0 → AGPL-3.0.**
- Angular 17 → 21. Building the frontend needs Node 20.19+ or 22.12+; the Docker images use Node 22.
- Backend dependencies upgraded to current releases, including FastAPI 0.142 (Starlette 1.7), Pydantic 2.13, sentence-transformers 6, NumPy 2 and Celery 5.6.
- JWTs are handled by PyJWT instead of python-jose, and passwords by `bcrypt` directly instead of passlib. Existing password hashes and sessions keep working.
- Tokens without an expiry are refused.
- Docker base images: Caddy 2.11, Node 22.

### Removed
- Unused dependencies: `openai`, `tiktoken`, `aiofiles`, `python-jose`, `passlib`.
