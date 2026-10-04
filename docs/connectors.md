# Connectors

The **Sources** page connects knowledge where it already lives. **The app never contacts a service on its own:**

- **Only the addresses you give.** No connector has a built-in web address. Each contacts only what a user typed into it, and refuses anything else, including redirects and links inside API responses.
- **Only when you click Sync now.** Nothing syncs on a schedule or at startup.
- **Credentials are encrypted.** Tokens are stored encrypted, decrypted only during a sync, and never sent back to the browser.

| Source | What you provide | What's ingested |
|---|---|---|
| Local folder *(server admins)* | A folder path inside `CONNECTOR_ROOTS` | Meeting captions (`.vtt`/`.srt` from Zoom, Teams or Meet, turned into "Speaker: text"), plus `.md`, `.txt`, `.pdf`, `.docx` |
| Git ADRs *(server admins)* | A local clone's path inside `CONNECTOR_ROOTS` | Each Architecture Decision Record becomes a decision, with no LLM. "Superseded by" / "Supersedes" / "Amends" lines become lineage links. Dates and authors come from the ADR, or the commit that added it |
| GitHub | The repository link, e.g. `https://github.com/acme/api` or a GitHub Enterprise link; a token for private repos | Merged pull requests and their discussion |
| Jira | Your Jira site, e.g. `https://acme.atlassian.net`; an API token (plus email for Cloud); a JQL query | Issues and their comments |
| Linear | Linear's API address, `https://api.linear.app/graphql`; a personal API key | Issues and their comments |

A github.com link is read through GitHub's API host, `api.github.com`; an Enterprise link through `<your host>/api/v3`.

- **Syncing is safe to repeat.** Each source document becomes one artifact with a fixed id. Unchanged documents (same content hash) are skipped without calling the LLM. A changed document updates its artifact in place, so items whose text didn't change keep their review state and lineage.
- **One bad document doesn't stop the rest.** Failures are listed on the connector's card.
- **Where syncs run.** In production, the API only queues the sync (**Queued…**) and the Celery worker runs it. If the worker dies mid-sync, the card shows **Interrupted** until the task is re-queued and finishes. In development they run inside the API process.
- **Removing a connector** keeps what it imported, unless you choose to delete that too.
- **In a group,** connectors belong to the group: every member sees them and can sync them, using the stored credentials. The card shows who added each one.
