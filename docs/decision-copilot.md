# Decision Copilot

The Decision Copilot puts the decisions a change touches where the change is being discussed. It works on **GitHub pull requests**, **Jira issues** and **Linear issues**, through the connector you already use to sync them, and in **Slack** as a `/decisions` command (see [Slack](#slack)).

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

## Developer portals

To show the same knowledge in an internal developer portal, a wiki or a CI check, create a **read-only API key** on the Copilot page under *Developer portals*. A portal can then call `POST /knowledge/impact` or `POST /knowledge/graphrag/query` with it. The key reads one space and changes nothing ([API keys](api.md)).

## Slack

In Slack, anyone can ask before making a change:

```
/decisions move the payment service to MongoDB
```

The answer covers what the record says, as in a comment:
- decisions on record and any the change may conflict with
- the systems it reaches
- risks and lessons

**Who sees it:** only the person who asked, unless they type `/decisions share …`, which posts it in the channel. `/decisions` alone or `/decisions help` explains the command.

**Least privilege.** A slash command answers through the reply address Slack sends with each request, so:
- Knowledge Hubs holds **no bot token** and can't read or post anything in Slack on its own.
- It stores only the app's **signing secret**, encrypted.
- Every request must carry Slack's signature (`X-Slack-Signature`, an HMAC-SHA256 of `v0:<timestamp>:<body>`) with a timestamp under five minutes old.
- The reply address must be `https://hooks.slack.com/…`, or nothing is sent.

**Setting it up**, on the **Copilot** page under *Slack*:
1. At [api.slack.com/apps](https://api.slack.com/apps), create an app (from scratch) in your workspace.
2. Copy its *Signing Secret* (Basic Information) and add it in Knowledge Hubs, with a name for the workspace.
3. Under *Slash Commands*, create `/decisions` with the request URL Knowledge Hubs shows.
4. Install the app to the workspace.

**Which space answers:** each Slack app belongs to one space, and answers from that space's knowledge. To answer from an organization's shared knowledge, add the app while working in the organization's space.

**Timing:** Slack waits at most three seconds. Knowledge Hubs acknowledges at once ("Looking up …"), then replaces that with the answer. The answer is worked out on the Celery worker in worker mode, using the model of whoever added the app.
