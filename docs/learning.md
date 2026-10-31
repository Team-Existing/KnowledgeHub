# Learn: institutional memory

A newcomer shouldn't have to read hundreds of documents to understand why things are the way they are. Neither should anyone about to change a system. The **Learn** page builds the answers from the record. Each tab answers questions like these:

| Tab | Answers |
|---|---|
| **Knowledge map** | *What should I understand before modifying the authentication service?* *Why does our payment architecture look this way?* |
| **Tried and abandoned** | *Which approaches have we tried and abandoned?* |
| **Recurring mistakes** | *What are our most common production mistakes?* |
| **Controversial** | *Which architectural decisions are controversial?* |
| **Ask** | Anything else, in plain English, through GraphRAG with citations |

None of it calls a model. It comes from statuses, links, outcomes, health signals and embeddings already in the graph, so it's fast, and every entry links to the item it comes from.

## Knowledge map

Pick the systems you work on; the page remembers them in your browser. Until you pick some, it shows all of them. For each system:

- **What holds now:** its current decisions, why each was taken, the alternatives that weren't chosen, and whether any may be out of date.
- **How it came to be:** every decision about it, oldest first, with what superseded, reversed or amended it and the recorded outcomes.
- **Context:** open risks (and those that happened), lessons learned, commitments in flight with owners and due dates, the people who appear most in its knowledge, and its dependencies both ways.

A system's knowledge is everything linked to it on the item pages, plus everything that names it or one of its aliases. Systems and aliases are set up on the [Systems](impact-analysis.md#systems) page.

## Tried and abandoned

- **Decisions that were superseded, reversed, invalidated or deprecated**, with what replaced each and why.
- **Decisions whose outcome was not achieved.**
- **Options considered and not chosen**: the alternatives recorded with decisions ("PostgreSQL *instead of* MongoDB").
- **Commitments that were dropped.**

## Recurring mistakes

The pool is lessons learned plus risks that happened (marked materialized, or with a "happened" link).

- **Grouping:** items about the same thing (embedding similarity of at least 0.6) form one group.
- **Order:** the most repeated mistakes come first, and among equals the most recent.
- **Systems:** each group shows the systems it touches.

## Controversial decisions

Decisions with signs of disagreement, ranked by how many signs they show:
- reversed, or reversing an earlier decision
- amended at least twice
- part of a decision history of three or more steps
- contradicted by newer knowledge, or flagged as possibly outdated
- an outcome that was not, or only partly, achieved

## Ask

The Ask tab suggests the questions above, naming your systems. Each one opens GraphRAG with the question ready to send.

The same views are available to developer portals through a read-only [API key](api.md): `GET /knowledge/learning/map?system=<id>`, `/abandoned`, `/mistakes` and `/controversial`.
