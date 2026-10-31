# Features

| Page | What you do there |
|---|---|
| **Hub** | Add knowledge: paste text, upload a file (PDF, TXT, MD, DOCX), fetch a URL, or submit a transcript, email thread or Slack export. Browse everything in the space, see the graph, cross-link items, and **Export** / **Import** the space as an OKF (Open Knowledge Format) JSON file |
| **Search** | Full-text search over items and sources, filtered by type, source and tag |
| **Review** | Accept, edit or reject extracted items. Everything starts `pending`; rejected items are never used by GraphRAG |
| **Decisions** | The decision log and risk register, with each item's status and what replaced it |
| **Health** | What may be out of date: items whose source changed, that build on a reversed decision, that someone flagged, or whose review date is near. Confirm them, flag them or set review dates ([Knowledge health](knowledge-health.md)) |
| **Playbooks** | Repeatable procedures (a release, onboarding), written as steps or built from how-tos and checklists already in the space |
| **Sources** | Connectors that pull in transcripts, notes, ADRs, pull requests and issues |
| **Copilot** | Preview or post the comment a GitHub pull request or a Jira or Linear issue gets; turn on the webhook; recent runs; connect Slack's `/decisions` command |
| **Impact** | Describe a change; see the decisions, conflicts, systems, risks, lessons and commitments it touches |
| **Systems** | Services, databases and teams, their dependencies, and the knowledge about each |
| **Commitments** | Action items with owner, due date and status, overdue first; decisions' expected outcomes and verdicts |
| **Organizations** | Create organizations, invite people with links, manage roles and members |
| **Groups** | Create groups inside an organization, invite its members, answer invitations |
| **GraphRAG** | Ask questions; answers cite the items they used and flag superseded decisions |
| **Models** | Download, switch or remove local LLMs; re-embed the knowledge base |
| **Users** *(server admins)* | Create accounts, change roles, see inactive accounts and groups due for deletion |

**How knowledge is extracted:**

| Source | Extractor | Item types |
|---|---|---|
| Pasted text | Rule-based (keywords) | decision, how-to, lesson, risk, best-practice, checklist |
| Files and URLs | LLM, chunked (~6,000 chars per chunk, up to 8 chunks). Regex fallback for the whole document if no LLM answers, or per chunk if a reply can't be parsed | decision, action-item, how-to, best-practice, lesson, risk, plus checklists (always regex) |
| Transcripts, email, Slack | LLM, with a regex fallback | decision, action-item, risk |
| Git ADRs (connector) | None: each ADR *is* a decision | decision, with its status and lineage |

Each item records which extractor produced it, shown as a badge in the review queue.

**Search** uses ArcadeDB's Lucene indexes. Items match on title, tags and type; sources on title, author, tags and body text. Sources matched only in their body rank below metadata matches and come back with a snippet.

**Cross-linking** (the Hub's **Cross-link** button) connects items from different sources whose titles share enough keywords (Jaccard similarity ≥ 0.12), as `RELATED_TO` edges.

The app checks `GET /health` every minute and shows a banner if the backend or the database can't be reached.
