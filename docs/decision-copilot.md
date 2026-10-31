# PR Copilot

The PR Copilot puts the decisions a pull request touches where the change is being made: one comment on the pull request, written from your knowledge base.

**It is advisory only.** It writes one comment per pull request and edits that comment when the pull request changes. It never approves, requests changes, labels, assigns, merges or edits code.

## What the comment says

The pull request's title, description and changed file paths go through [impact analysis](impact-analysis.md). The comment lists:

- **Decisions on record** about the same ground, each marked *may conflict*, *would change* or *consistent* (when a model assessed it), with the model's one-line reason, why the decision was taken, and what replaced it if it's history.
- **Systems:** the systems the PR touches and the ones that depend on them. Systems are found by name: `services/payments/store.py` names *Payments*, so add your systems' names and aliases on the **Systems** page.
- **Known risks and lessons learned** tied to those systems.
- **A warning at the top** when the change may conflict with a recorded decision.

Each entry links to its page in Knowledge Hubs when `PUBLIC_URL` is set.

**When it comments.** It only comments when the record has something to say:
- a current decision the change conflicts with or changes, or
- a decision, risk or lesson tied to a system the PR touches (linked or named), unless the model judged it unrelated.

A README typo gets no comment.

## Setting it up

1. **Add a GitHub connector** for the repository (on **Sources**). Its token needs to write pull request comments. For a fine-grained token, that's *Pull requests: read and write*. The connector's sync keeps ingesting merged pull requests as before.
2. **Open PR Copilot**, from the navigation or the connector's *PR Copilot* button.
3. **Try it on a pull request:** enter its number and press **Preview** to see the comment. Nothing is posted until you press **Post the comment**.
4. **To comment automatically:**
   - Turn on *Comment automatically*. Knowledge Hubs shows a payload URL and a secret. The secret is shown only then: copy it.
   - In the repository on GitHub, go to *Settings → Webhooks → Add webhook*. Paste the URL and the secret, choose content type `application/json`, and select the *Pull requests* event.

From then on:
- **New and updated pull requests are analysed:** opened, reopened, new commits, ready for review, or description edited. Drafts and closed pull requests are skipped.
- **The comment is updated, not repeated:** the same comment is edited on each push, and a commit that was already analysed isn't analysed again.
- **Recent runs** lists each analysis: posted, updated, skipped (nothing relevant) or failed (with the reason, such as a token that can't write comments).

## Security

- **Signed webhooks only.** Every webhook must carry GitHub's `X-Hub-Signature-256`, an HMAC of the request body with the connector's secret, or it's refused. Unknown connectors get the same answer, so nothing can be probed.
- **Secret storage.** The secret is stored encrypted with `CREDENTIALS_KEY`, bound to the connector, and never returned again after it's shown. **Create a new secret** replaces it.
- **One host only.** The Copilot contacts nothing but the connector's own GitHub host, as with syncs.
- **Response time.** The webhook answers at once and analyses afterwards (on the Celery worker in worker mode), because GitHub gives up after 10 seconds. The analysis uses the model chosen by whoever added the connector.

## Limits

- **GitHub only for now.** Jira, Linear and Slack are planned next.
- **Where analyses run:** in worker mode (`REDIS_URL` set) webhook analyses run on the Celery worker; otherwise in the API process that received them. One pull request is analysed at a time, so two events arriving together post one comment.
