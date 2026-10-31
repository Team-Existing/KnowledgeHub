# Screenshots

Every page of the app, in sidebar order. The data is fictional: the "Platform team" group of a demo organization, Acme, with a few architecture reviews, a security review, an incident retro and a meeting's action items. The GraphRAG answer and the impact analysis were produced by the local model, llama3.1:8b. See [Features](features.md) for what each page does.

## Hub

Add knowledge from text, files, URLs or transcripts, and browse everything in the space.

![Hub: the ingest panel and the list of sources in the Platform team space](images/hub.png)

## Search

Full-text search over items and sources, filtered by type, source and tag.

![Search results for "database"](images/search.png)

## Review

Extracted items wait here until someone accepts, edits or rejects them. GraphRAG only uses accepted items.

![Review queue with three pending items](images/review.png)

## Decisions

The decision log and risk register, with each item's status and the decision it supersedes or amends.

![Decision log showing active decisions and what each one replaced](images/decisions.png)

## An item's page

Everything about one item: its details and source, plus:
- its [commitment](commitments.md) (for an action item) or its [outcome](commitments.md#outcomes) (for a decision)
- the [systems](impact-analysis.md#systems) it affects
- whether it [may be out of date](knowledge-health.md)
- its history and links to other items

![A decision's page: details, source, and its expected outcome waiting for a verdict](images/item.png)

![An action item's page: its commitment status, owner and due date](images/commitment.png)

## Commitments

What decisions set in motion: owner, due date and status, with overdue ones first. The Outcomes tab lists decisions waiting for a verdict on whether they worked, and the recorded verdicts.

![Commitments: three open, one overdue, each linked to the decision it carries out](images/commitments.png)

![Outcomes: one decision due for a verdict, two recorded as achieved and partly achieved](images/outcomes.png)

## Systems

Services, databases and teams, the other names they go by, and what depends on what. Impact analysis follows these dependencies.

![Systems: six systems, with Payments selected showing its owner, aliases and dependencies](images/systems.png)

## Health

Current knowledge that may be out of date, on evidence, or that is due for review.

![Health: a decision flagged as possibly outdated, and one due for review](images/health.png)

## Playbooks

Repeatable procedures, written as steps or built from how-tos and checklists in the space.

![Two playbooks: Production release and On-call handover](images/playbooks.png)

## GraphRAG

Ask questions in plain English. Answers cite the items they used and point out decisions that were replaced.

![GraphRAG answering "What do we use for domain events now, and why did it change?" with citations, a superseded decision and the recorded outcome](images/graphrag.png)

## Impact

Before changing course, describe the change and see what the record says:
- decisions on record, and any it may conflict with
- their recorded reasoning
- the systems it reaches
- risks, lessons and commitments in flight

![Impact analysis of "Move the payment service from PostgreSQL to MongoDB": a conflict with the PostgreSQL decision, the systems reached, a risk, a lesson and a commitment in flight](images/impact.png)

## Copilot

The Decision Copilot:
- comments on GitHub pull requests and Jira and Linear issues with the decisions they touch
- powers Slack's `/decisions` command

Preview a comment, turn on the webhook, and see recent runs.

![Decision Copilot for a GitHub repository, with automatic comments turned on](images/copilot.png)

## Sources

Connectors for folders, git ADRs, GitHub, Jira and Linear. They sync only when someone clicks **Sync now**.

![Three configured connectors: GitHub, Jira and Linear](images/sources.png)

## Organizations

The organization: members and their roles, invite links, and joining by email domain.

![Acme: invite links and the organization's settings](images/organizations.png)

## Groups

Groups inside an organization: invite its members and share a space with them.

![The Platform team group with three members and one pending invitation](images/groups.png)

## Models

Download, switch or remove the local LLMs, use hosted ones if the server offers them, and re-embed the knowledge base.

![Model manager: Llama 3.1 8B in use, Mistral 7B and GPT-OSS 20B available to download](images/models.png)

## Account

Your verified email and the single sign-on providers connected to your account.

![Account page: sign-in methods](images/account.png)

## Users

Server admins create accounts, change roles and see which accounts and groups are due for deletion.

![Users page: account creation, the inactivity clean-up status and the accounts](images/users.png)
