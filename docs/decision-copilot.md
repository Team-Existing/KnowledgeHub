# Decision Copilot

The Decision Copilot puts the decisions a change touches where the change is being discussed. It works on **GitHub pull requests**, **Jira issues** and **Linear issues**, through the connector you already use to sync them.

**It is advisory only.** It writes one comment per pull request or issue and edits that comment when the change evolves. It never approves, requests changes, transitions, assigns, labels or merges anything.

## What the comment says

The pull request's or issue's title and description, plus a pull request's changed file paths, go through [impact analysis](impact-analysis.md). The comment lists:

- **Decisions on record** about the same ground. Each is marked *may conflict*, *would change* or *consistent* (when a model assessed it), with the model's one-line reason, why the decision was taken, and what replaced it if it's history.
- **Systems:** the ones the change touches, and the ones that depend on them. Systems are found by name: `services/payments/store.py` or "the payment service" both name *Payments*, so add your systems' names and aliases on the **Systems** page.
- **Known risks and lessons learned** tied to those systems.
- **A warning at the top** when the change may conflict with a recorded decision.

Entries link to their page in Knowledge Hubs when `PUBLIC_URL` is set.

**The format follows the platform:**

| Platform | Comment format | How it finds its own comment again |
|---|---|---|
| GitHub | Markdown | a hidden marker |
| Linear | Markdown | the id of the comment it posted last |
| Jira | wiki markup (REST API v2, served by Cloud, Server and Data Center) | the id of the comment it posted last |

If someone deletes that comment, the next run posts a new one.

**When it comments.** It only comments when the record has something to say:
- a current decision the change conflicts with or changes, or
- a decision, risk or lesson tied to a system the change touches (linked or named), unless the model judged it unrelated.

A README typo gets no comment.

## Setting it up

1. **Add a connector** for the repository, Jira site or Linear workspace (on **Sources**), with credentials that can write comments:
   - **GitHub:** a fine-grained token with *Pull requests: read and write*.
   - **Jira:** an account allowed to add comments in the project.
   - **Linear:** an API key allowed to create comments.

   The connector's sync keeps working as before.
2. **Open Copilot,** from the navigation or the connector's *Copilot* button.
3. **Try it:** enter a pull request number, a Jira key (`ARCH-12`) or a Linear identifier (`ENG-42`) and press **Preview** to see the exact comment. Nothing is posted until you press **Post the comment**.
4. **To comment automatically,** turn on *Comment automatically* and create the platform's webhook with the URL shown:

| Platform | Where | Events | Secret |
|---|---|---|---|
| GitHub | repository *Settings → Webhooks*, content type `application/json` | Pull requests | created by Knowledge Hubs and shown once: paste it into GitHub |
| Jira | *Settings → System → WebHooks* | Issue created, issue updated | created by Knowledge Hubs and shown once: paste it into Jira. From Jira Automation's *Send web request*, which can't sign, send it in a header `X-Knowledge-Hubs-Token` instead |
| Linear | *Settings → API → Webhooks* | Issues | created by Linear: paste Linear's signing secret into Knowledge Hubs |

From then on:
- **Changes are analysed when they're opened or edited.**
  - Skipped: drafts, closed pull requests, and finished or cancelled issues.
  - An update that changes nothing the Copilot reads is not analysed again. That includes the Copilot's own comment, which counts as an "issue updated" event, so it can't loop.
- **Recent runs** lists each analysis: posted, updated, skipped (nothing relevant) or failed (with the reason, such as credentials that can't write comments).

## Security

- **Every webhook is verified, or refused:**
  - **GitHub:** `X-Hub-Signature-256`, an HMAC-SHA256 of the body with the secret.
  - **Jira:** `X-Hub-Signature` with the same scheme, or the secret itself in `X-Knowledge-Hubs-Token`.
  - **Linear:** `Linear-Signature`, an HMAC-SHA256 of the body. The delivery's `webhookTimestamp` must be under five minutes old, so old deliveries can't be replayed.
- **Nothing to probe:** unknown connectors and wrong platforms get the same answer as a bad signature.
- **Secret storage:** secrets are stored encrypted with `CREDENTIALS_KEY`, bound to the connector, and never returned. A created secret is shown once; **Create a new secret** replaces it.
- **One host only:** the Copilot contacts nothing but the connector's own address, as syncs do.
- **Where the work happens:** webhooks are answered at once. The analysis runs afterwards: on the Celery worker in worker mode (`REDIS_URL` set), otherwise in the API process.
  - One analysis per pull request or issue runs at a time, so two events arriving together post one comment.
  - The analysis uses the model chosen by whoever added the connector.

## Limits

- **Slack** is planned next: answers in a thread when someone asks about a change.
