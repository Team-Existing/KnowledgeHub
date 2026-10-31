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

## What you do about it

- **The Health page** lists everything that needs a look: items that may be out of date first (those with the most signals first), then review dates, soonest first.
- **On an item's page**, the *Is this still true?* panel shows the signals and offers:
  - **Still valid**: you checked and it holds. This clears the signals, records who confirmed it and when, and optionally sets the next review date.
  - **Flag as possibly outdated**, with a reason the next reader will see.
  - **Review by**: a date to look at it again, for example a decision based on this year's pricing.
- If it no longer holds, record what replaced it as usual: link the newer decision (*supersedes*, *reverses*) or declare a status.

Every signal, confirmation and review date is in the item's history, with who did it.

## Answers

GraphRAG still uses an item that may be out of date, but marks it as such in what the model sees, and the model is told to say so. The source list shows **may be out of date** next to it.

## Why items are kept when they leave their source

Item ids come from their source and title. Before health tracking, rewording a decision in an ADR gave it a new id: the accepted item was deleted with its lineage links and history, and the reworded one waited in the review queue as if it were new. Now the accepted item stays, flagged *no longer in its source*, so you can link the new version to it (*supersedes*) or confirm it. Items nobody reviewed or linked are still replaced silently.
