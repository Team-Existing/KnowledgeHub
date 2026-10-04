# Contributing to Knowledge Hubs

Thanks for your interest in improving Knowledge Hubs. Bug reports, documentation fixes and code are all welcome.

## Before you start

- **Bugs and small fixes:** open a pull request directly, or an issue first if you'd like to discuss it.
- **New features or larger changes:** please open an issue describing the problem and your approach before writing much code, so we can agree on the design first.
- **Security issues:** don't open a public issue. See [SECURITY.md](SECURITY.md).
- **Looking for something to do?** Issues labelled `good first issue` are a good start, and [Known limitations](docs/known-limitations.md) lists gaps worth closing.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

Follow [Run it from source](docs/getting-started.md#run-it-from-source): ArcadeDB in Docker, Ollama, the backend with `uvicorn --reload` and the frontend with `npm start` (http://localhost:4200, live reload). You need Python 3.11, Node 20.19+ (or 22.12+) and Docker.

The [architecture overview](docs/architecture.md) explains how the code is organised.

## Running the tests

The backend tests run against a real ArcadeDB, in a throwaway database they create and drop. The LLM and embeddings are faked, so Ollama isn't needed.

```bash
# a test ArcadeDB (once)
docker run -d --name kh-test-arcadedb -p 2480:2480 \
  -e JAVA_OPTS="-Darcadedb.server.rootPassword=testpass123" arcadedata/arcadedb:26.9.1

cd backend
pip install -r requirements-dev.txt
KH_TEST_ARCADEDB_PASSWORD=testpass123 pytest          # PowerShell: $env:KH_TEST_ARCADEDB_PASSWORD="testpass123"; pytest

cd ../frontend
npm test -- --watch=false --browsers=ChromeHeadless   # needs Chrome
npm run build
```

Optional: set `KH_TEST_REDIS_URL=redis://localhost:6379/15` (with a Redis running) to include the Redis tests. See [Testing](docs/testing.md) for what each suite covers and how to run the live integration tests.

CI runs all of this on every pull request.

## Guidelines

- **Match the surrounding code**: its naming, comment style and structure. Comments explain *why*, not what.
- **Add tests** for bug fixes and new behaviour. Security-relevant code (spaces, permissions, credentials, connectors, input handling) needs tests that show the protection works.
- **Keep the frontend and backend in step.** [`test_frontend_contract.py`](backend/tests/test_frontend_contract.py) fails if a backend endpoint has no caller in the UI or a UI call has no endpoint. A new endpoint ships together with the UI that uses it (or is listed in `NOT_FOR_THE_UI` with a reason).
- **Keep it local-first.** The app must not contact any service the user didn't configure. New network access must be opt-in and limited to the addresses a user provides.
- **Data is scoped to a space.** Every query must filter on the active space; see [`backend/app/spaces.py`](backend/app/spaces.py).
- **Update the docs** in [`docs/`](docs/) when you change behaviour, configuration or the API, and add a line to [CHANGELOG.md](CHANGELOG.md) under *Unreleased*.
- **Dependencies** are pinned to exact versions. Explain any new dependency in your pull request; prefer the standard library or what's already used.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Make focused commits with clear messages (what changed and why).
3. Make sure the tests and the frontend build pass.
4. Open a pull request and fill in the template.

A maintainer will review it. Please be patient: this is a volunteer project.

## License

Knowledge Hubs is licensed under the [GNU AGPL v3.0](LICENSE). By contributing, you agree that your contributions are licensed under the same license.
