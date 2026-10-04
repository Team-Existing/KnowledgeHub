# Screenshots

Every page of the app, in sidebar order. The data is fictional: a demo "Platform team" with a few meeting notes, a retrospective, a release process and a security review. See [Features](features.md) for what each page does.

## Hub

Add knowledge from text, files, URLs or transcripts, and browse everything in the space.

![Hub: the ingest panel and the list of artifacts in the space](images/hub.png)

## Search

Full-text search over items and sources, filtered by type, source and tag.

![Search results for "database": a best practice and two meeting notes](images/search.png)

## Review

Extracted items wait here until someone accepts, edits or rejects them. GraphRAG only uses accepted items.

![Review queue with three pending items](images/review.png)

## Decisions

The decision log and risk register, with each item's status and the decision it supersedes or amends.

![Decision log showing active decisions and what each one replaced](images/decisions.png)

## Playbooks

Repeatable procedures, written as steps or built from how-tos and checklists in the space.

![Two playbooks: Production release and On-call handover](images/playbooks.png)

## GraphRAG

Ask questions in plain English. Answers cite the items they used and point out decisions that were replaced.

![GraphRAG answering "What do we use for domain events now, and why did it change?" with citations, including a superseded decision](images/graphrag.png)

## Sources

Connectors for folders, git ADRs, GitHub, Jira and Linear. They sync only when someone clicks **Sync now**.

![Three configured connectors: GitHub, Jira and Linear](images/sources.png)

## Groups

Create groups, invite people and share a space with them.

![The Platform team group with three members and one pending invitation](images/groups.png)

## Models

Download, switch or remove the local LLMs, and re-embed the knowledge base.

![Model manager: Llama 3.1 8B in use, Mistral 7B and GPT-OSS 20B available to download](images/models.png)

## Users

Server admins create accounts, change roles and see which accounts and groups are due for deletion.

![Users page: account creation, the inactivity clean-up status and four accounts](images/users.png)
