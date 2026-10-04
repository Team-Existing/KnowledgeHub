# Decision tracking

Every **decision** and **risk** has a lifecycle status:

| Type | Statuses |
|---|---|
| Decision | `active` (default), `proposed`, `superseded`, `reversed`, `deprecated`, `rejected` |
| Risk | `open` (default), `mitigated`, `materialized`, `closed` |

Items are linked newer → older:

| Link | Between | Effect |
|---|---|---|
| supersedes | decision → decision | the older decision becomes `superseded` |
| reverses | decision → decision | the older decision becomes `reversed` |
| amends | decision → decision | part of the history; no status change |
| realizes | any → risk | the risk happened: `materialized` |
| mitigates | any → risk | `mitigated` |
| learned from | lesson → any | the lesson came out of that item |

- **Links set the status.** Delete the link (or the newer item) and the older one goes back automatically.
- **You can also declare a status by hand,** e.g. a decision dropped without a replacement, or a risk that's closed. A link still takes priority over a declared status.
- **Invalid links are refused:** an item linked to itself, a pair linked twice, the wrong types (a lesson can't supersede a decision), or a circular history.
- **History.** Reviews, edits, links and status changes are logged per item, with who did them.
- **Suggestions.** Each item's page proposes probable links from similar items in other sources, e.g. "this newer decision probably supersedes that 2023 one". One click accepts.
- **Everywhere else:** status badges appear on the Hub, Search, Review and GraphRAG sources. GraphRAG presents the replacement as the current decision.

Where to see it: the **History & lineage** panel on each item's page, and the **Decisions** page.
