# Knowledge Hubs

[![CI](https://github.com/Team-Existing/KnowledgeHub/actions/workflows/ci.yml/badge.svg)](https://github.com/Team-Existing/KnowledgeHub/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

A **local-first knowledge base** for teams. It turns meeting notes, retros, decision logs, documents, web pages and transcripts into structured knowledge (decisions, risks, lessons, how-tos) that you can review, search, track over time and question in plain English.

- **Runs on your machine.** FastAPI backend, Angular frontend, **ArcadeDB** as the only data store, and local AI: one of three LLMs run by [Ollama](docs/local-models.md) plus sentence-transformers embeddings. There are no cloud AI providers.
- **Personal and group spaces.** Everyone has a private space and can create or join groups that share one. A switcher in the nav picks where you're working.
- **Decisions have a history.** A decision can supersede, reverse or amend an older one, and risks can be mitigated or actually happen. The app tracks each item's status and shows how it evolved.
- **Connects only where you point it.** Knowledge can come from folders, git repositories (ADRs), GitHub, Jira and Linear, but only from addresses a user enters, and only when they click **Sync now**.
- **One command to try it, a resilient stack for production.** `docker compose up` runs everything on one machine. [`deploy/`](deploy/) runs it behind a TLS load balancer, with two app instances, a Celery worker and a three-node ArcadeDB cluster.

| Page | What you do there |
|---|---|
| **Hub** | Add knowledge: paste text, upload a file (PDF, TXT, MD, DOCX), fetch a URL, or submit a transcript, email thread or Slack export. Browse, see the graph, cross-link items, export and import the space |
| **Search** | Full-text search over items and sources, filtered by type, source and tag |
| **Review** | Accept, edit or reject extracted items before GraphRAG uses them |
| **Decisions** | The decision log and risk register, with each item's status and what replaced it |
| **Playbooks** | Repeatable procedures, written as steps or built from how-tos and checklists in the space |
| **Sources** | Connectors that pull in transcripts, notes, ADRs, pull requests and issues |
| **GraphRAG** | Ask questions; answers cite the items they used and flag superseded decisions |

More in [Features](docs/features.md).

## Quick start

Requires [Docker](https://docs.docker.com/get-docker/) (Compose v2.20+), about 15 GB of disk and 8 GB+ of RAM.

```bash
git clone https://github.com/Team-Existing/KnowledgeHub.git
cd KnowledgeHub
docker compose up --build
```

Open **http://localhost:8080**, create an account (the first one is the server admin) and pick a model to download. No configuration file is needed: secrets are generated on first start.

To use a GPU, an Ollama you already run, or to work on the code with live reload, see [Getting started](docs/getting-started.md).

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Docker in one command, running from source, migrating from the SQLite version |
| [Features](docs/features.md) | What each page does, how knowledge is extracted, search and cross-linking |
| [Production deployment](docs/deployment.md) | The multi-instance stack in `deploy/`: TLS, load balancing, worker, ArcadeDB cluster, operations |
| [Configuration](docs/configuration.md) | Every environment variable |
| [Accounts, roles and spaces](docs/accounts-and-spaces.md) | Server admins, groups, personal and shared spaces |
| [Decision tracking](docs/decision-tracking.md) | Statuses, lineage links and history |
| [Connectors](docs/connectors.md) | Folders, git ADRs, GitHub, Jira, Linear |
| [Inactive accounts and groups](docs/retention.md) | Automatic deletion and its safety rails |
| [Local models](docs/local-models.md) | The supported LLMs and how they're chosen |
| [Architecture](docs/architecture.md) | Components, data model, the GraphRAG pipeline |
| [API](docs/api.md) | Every endpoint |
| [Security notes](docs/security.md) | How accounts, data, credentials and inputs are protected |
| [Testing](docs/testing.md) | Running the backend and frontend test suites |
| [Known limitations](docs/known-limitations.md) | What it doesn't do (yet) |

## Contributing

Contributions are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers setting up a development environment, running the tests and opening a pull request. The [known limitations](docs/known-limitations.md) are a good place to look for something to work on.

Please report security issues privately, as described in [SECURITY.md](SECURITY.md), not in public issues.

## License

Knowledge Hubs is licensed under the [GNU Affero General Public License v3.0](LICENSE). If you run a modified version as a network service, you must offer its source code to its users; the app's sidebar links to the source for this reason (`SOURCE_CODE_URL` in [`frontend/src/app/app.component.ts`](frontend/src/app/app.component.ts)).
