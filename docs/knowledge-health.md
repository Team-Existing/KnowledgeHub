# Knowledge health

Knowledge is most dangerous when it's out of date but still looks authoritative. A decision's [status](decision-tracking.md) says whether it was *replaced*; its **health** says whether it may have *gone out of date while still current*.

Health never comes from age alone. An item is only questioned on evidence:

| Signal | Raised when |
|---|---|
| **Source changed** | The source an accepted item was extracted from changes (a connector syncs a new version, or someone edits the source) |
| **No longer in its source** | The source changes and no longer contains the item. Accepted items, and items with a decision history, are kept and flagged rather than deleted |
| **Something it builds on changed** | The decision it amends is reversed or superseded; a decision that mitigates it (a risk) is reversed or superseded; the risk it mitigates happens anyway |
| **Flagged** | Someone marks it as possibly outdated, with a reason |
| **Outcome not achieved** | A decision's recorded [outcome](commitments.md) is *not achieved* |
| **Contradicted** | Newer knowledge says otherwise (see [Verification](#verification)). The older item is flagged, naming the newer one |
| **Possible duplicate** | An item is nearly the same text as an older one. The newer one is flagged |

Each item then has one of three states:

| State | Meaning |
|---|---|
| **May be out of date** | At least one signal |
| **Needs review** | No signal, but its review date is within 14 days or has passed |
| **Up to date** | Neither |

Items whose status already says they no longer hold (superseded, reversed, rejected, deprecated, closed, and commitments that are done or dropped) aren't questioned. Items still waiting in the review queue aren't flagged when their source changes, because they will be reviewed anyway.

## One lifecycle

Every item also has a one-word **lifecycle**, combining its status and its health. Its page shows it.

| Lifecycle | Meaning |
|---|---|
| **Active** | Still considered valid |
| **Needs review** | Approaching its review date |
| **Potentially stale** | Evidence says its assumptions may have changed (any signal above) |
| **Superseded** | Replaced by a newer decision (superseded or reversed) |
| **Invalidated** | No longer applicable: declared *invalidated* (or deprecated, rejected; a risk closed) |

## Verification

When an item is accepted in review, Knowledge Hubs compares it with similar current knowledge of the same type: decisions, best practices, lessons, risks and how-tos.

- **Possible duplicates:** items with nearly the same text (embedding similarity of at least 0.92) are flagged. No model is needed.
- **Contradictions:** for up to five items on a similar topic (similarity of at least 0.55), one model call says whether the new item contradicts each. When it does, the **older** item is flagged *contradicted*, with the reason, because newer evidence says otherwise.

What isn't compared:
- items already linked to each other (a person has said how they relate)
- items that are already retired

Nothing else changes. A person decides whether the newer item supersedes the older one, and the decision page's link suggestions offer exactly that.

**Check against similar knowledge**, on an item's page, runs the same comparison on demand. `VERIFY_ON_ACCEPT=off` turns off the automatic check.

### Unsupported claims

A model can extract a decision its source never states, or change one ("stop the nightly export" from "we keep the nightly export"). So after a model extracts items from a document, transcript or conversation, each one is checked against that source **before anyone reviews it**:

1. **The text.** Knowledge Hubs finds the passage of the source (one or two sentences) closest to the claim. If the passage has every content word of the claim, and both are negated or neither is, the claim is *supported*. No model is needed. Having most of the words in common isn't enough.
2. **The model.** The other claims go to one model call (ten at a time), each with its two closest passages. The model says whether the passages state the claim: *supported*, *partly* (some of it was added or changed) or *unsupported*, with a one-sentence reason.

The review queue shows the verdict on each card, quoting the passage when it isn't a plain match. The item's page shows it too, with **Check against its source** to run it again. An *unsupported* item also gets a health signal, so it isn't forgotten once accepted; a later *supported* verdict clears it.

What isn't checked:
- items found by the rule-based extractor: they quote a sentence of their source as it is
- checklists
- structured sources (ADRs, issues), which state their items explicitly

Without a model, claims the text doesn't cover stay *not checked*. `CHECK_SUPPORT=off` turns the check off.

## What you do about it

- **The Health page** lists everything that needs a look: items that may be out of date first (those with the most signals first), then review dates, soonest first.
- **On an item's page**, the *Is this still true?* panel shows the signals and offers:
  - **Still valid**: you checked and it holds. This clears the signals, records who confirmed it and when, and optionally sets the next review date.
  - **Flag as possibly outdated**, with a reason the next reader will see.
  - **Review by**: a date to look at it again, for example a decision based on this year's pricing.
- If it no longer holds, record what replaced it as usual: link the newer decision (*supersedes*, *reverses*) or declare a status.

Every signal, confirmation and review date is in the item's history, with who did it.

## The weekly digest

On the Health page, under *Weekly digest*, a space can send a weekly summary of what needs attention to one or more Slack channels. It covers:
- what may be out of date (with the latest reason)
- reviews due within two weeks
- overdue commitments
- decisions past their check-by date with no verdict

**Setting it up:**
1. In Slack, create an app with *Incoming Webhooks* turned on, and add a webhook for the channel.
2. Paste the webhook URL and pick the weekday.

**When it's sent:**
- It goes out at `DIGEST_HOUR` (UTC, default 8).
- **Send now** sends it at once.
- A week with nothing to report sends nothing.
- The page previews what the digest would say right now.

**Security and scheduling:**
- The URL must be a Slack incoming webhook (`https://hooks.slack.com/services/…`). It's stored encrypted and never shown again.
- The scheduled check runs hourly: on Celery beat in worker mode, otherwise in the API process. A shared lock means no digest goes out twice. `DIGEST_ENABLED=false` stops scheduled sending.

## Answers

GraphRAG still uses an item that may be out of date, but marks it as such in what the model sees, and the model is told to say so. The source list shows **may be out of date** next to it.

## Why items are kept when they leave their source

Item ids come from their source and title. Before health tracking, rewording a decision in an ADR gave it a new id: the accepted item was deleted with its lineage links and history, and the reworded one waited in the review queue as if it were new. Now the accepted item stays, flagged *no longer in its source*, so you can link the new version to it (*supersedes*) or confirm it. Items nobody reviewed or linked are still replaced silently.
