# Commitments and outcomes

Decisions set things in motion: *migrate the payment service by December*, *complete a security review*. Months later, someone asks whether the decision did what it was supposed to. Knowledge Hubs tracks both halves.

## Commitments

A **commitment** is an action item, the kind extracted from meetings, transcripts and email threads ("Omar migrates the tables by 1 December"). Each one has:

| | |
|---|---|
| **Status** | open, in progress, done or dropped. Change it on the item's page or straight from the **Commitments** list |
| **Owner** | a person or team. It starts from whoever extraction found and can be changed |
| **Due date** | taken from extraction when the source gives a date. A due date in words ("end of sprint") is shown as written until someone sets a real date |
| **The decision it carries out** | an *implements* link (on the item's page: *Carries out the decision …*). Knowledge Hubs suggests the decision from the same meeting |

A commitment is **overdue** when its due date has passed and it's neither done nor dropped. The **Commitments** page lists them most pressing first: overdue, then by due date, then those without a date. Filters show open, overdue, finished or all.

Finished commitments (done or dropped) are left out of the Health page, like superseded decisions.

## Outcomes

On a decision's page, under **Outcome**:

1. **Say what it should achieve** ("no lost transactions after the move"; "p95 latency under 100 ms") and, optionally, **when to check**.
2. When the time comes, **record whether it worked**: *achieved*, *partly achieved* or *not achieved*, with the evidence in a sentence or two.

The **Outcomes** tab of the Commitments page lists:
- **Decisions waiting for a verdict**, with those past their check date first.
- **Recorded outcomes**, newest first.
- **A tally:** how many decisions were achieved, partly achieved, not achieved, or are still waiting.

Next to each decision is the evidence the graph already holds:
- how many of its commitments are done and how many are overdue
- the lessons learned from it (*learned from* links)

**A decision whose outcome was not achieved is flagged on the Health page**, because what it promised no longer holds. Someone can then confirm it still stands, or record what replaces it.

Everything (status changes, owners, due dates, expectations, verdicts) is in the item's history, with who did it.

## In answers

GraphRAG sees each commitment's owner and due date, including whether it's overdue, and each decision's expected outcome and verdict. It can answer "who owns the billing migration and is it late?" and "did moving billing to PostgreSQL work?" from the record.
