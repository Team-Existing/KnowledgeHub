# Impact analysis

Before changing course, say what you want to change ("move the payment service from PostgreSQL to MongoDB") and see what the record says about it:

- **Decisions on record:**
  - what the team decided on the same ground, and why
  - the alternatives it considered (extraction records them: "PostgreSQL *instead of* MongoDB", or an "Alternatives considered: …" line), who owns it, and its recorded outcome
  - how many commitments are still in flight
  - for decisions that are now history, what replaced them
- **Potential conflicts:** whether the change goes against a current decision, changes it, or fits it, with a one-line reason.
- **Systems reached:**
  - the systems the change touches
  - every system that depends on them, directly or through others, with the path ("depends on it through Payments → Reporting")
- **Risks, lessons and commitments in flight** on the same ground.

The analysis is read-only. It never changes anything.

Start it from the **Impact** page, from a decision's page (*What would changing this decision affect?*), or from a system on the **Systems** page.

## Systems

Impact analysis needs to know your systems: services, databases, libraries, infrastructure, teams and products. On the **Systems** page:

- **Add each system**, with its owner and any other names it goes by (aliases). The aliases matter: knowledge that mentions *any* of a system's names counts as being about it.
- **Record what depends on what.** If Reporting reads from Payments, Reporting *depends on* Payments, so a change to Payments reaches Reporting.
- **Or accept what your knowledge already says.** *Suggested from your knowledge* lists dependencies that items state, for example "Reporting reads from the payment service" or "the notifications service publishes to NATS". Each suggestion links to the item that says it.
  - A suggestion needs both names in one sentence, joined by a phrase such as *depends on*, *reads from*, *calls*, *uses* or *publishes to*.
  - Nothing negates it in between ("never calls"), and no other system is named in between.
  - **Add** records the dependency.
- **Link knowledge to systems** on an item's page, under **Systems**. An item that names a system you haven't linked it to gets a one-click *Link it*.

## How each entry gets into the report

Every entry says why it's there:

| Reason | Meaning |
|---|---|
| *named in the proposal* / *chosen* / *the decision affects it* | how a system became a starting point |
| *depends on Payments* | reached through dependencies |
| *affects Payments* | the item is linked to a system the change reaches |
| *mentions Payments* | the item names that system (or one of its aliases) |
| *similar (0.62)* | the item is close in meaning to the proposal (cosine similarity at least 0.45) |
| *carries out …* | a commitment of a related decision, or of the decision being reconsidered |

All of that comes from the graph and plain matching.

**The model's part** is one LLM call. It reads the proposal against up to 8 current decisions, with their reasoning, and labels each *conflicts*, *changes*, *consistent* or *unrelated*. Conflicts are listed first, and the page warns when there are any.

Without a model (none reachable, or `IMPACT_LLM=off`), the report is the same, except that decisions are unlabelled. The page then says they weren't assessed.

## Limits

- **Proposals name systems by matching words.** A proposal that describes a system without naming it ("the thing that charges cards") only reaches it if you tick the system, or through similar knowledge.
- **Dependencies come from people and from what the knowledge says.** Importing them from service catalogs or infrastructure code is not built yet.
