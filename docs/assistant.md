# Assistant

One box for any question. The **Assistant** page works out which parts of Knowledge Hubs can answer it, runs them, and checks the answer before you rely on it. It only reads: it never changes the record.

## The agents

| Agent | What it does | Also on its own page |
|---|---|---|
| **Research & answer** | Answers from the record, with citations (GraphRAG) | GraphRAG ([features](features.md)) |
| **Impact** | For a proposed change: the decisions it may conflict with or change, the systems it reaches, risks, lessons and commitments | [Impact](impact-analysis.md) |
| **Learning** | A system's map (what holds now and why), what was tried and abandoned, recurring mistakes, controversial decisions | [Learn](learning.md) |
| **Watchtower** | What may be out of date or is due for review, overdue commitments, outcomes waiting for a verdict | [Health](knowledge-health.md), [Commitments](commitments.md) |
| **Verification** | Checks each sentence of the answer against the sources it drew on | |

## How a question is handled

1. **Plan.** Rules read the question and pick the agents. No model call is needed, so the plan is fast and the same every time. The page shows each step with the reason it was chosen.

   | The question… | Agent |
   |---|---|
   | describes a change ("should we", "migrate", "replace", "move off", "adopt", …) | Impact |
   | asks what to understand about a system it names ("before changing Payments", "why does…") | Learning: that system's map |
   | asks what was tried, abandoned or not chosen | Learning: tried and abandoned |
   | asks about mistakes, incidents or lessons | Learning: recurring mistakes |
   | asks about disagreement or controversy | Learning: controversial |
   | asks what needs attention ("out of date", "overdue", "commitments", "outcomes", …) | Watchtower |
   | is a why/what/how question, or fits none of the above | Research, then Verification |

   A question can bring in several agents: *"Should we migrate billing? Anything overdue?"* runs Impact, Watchtower, Research and Verification.

2. **Run.** The steps run in order. Each shows its result and how long it took. A step that fails is reported, and the others still run.

3. **Verify.** Each sentence of the answer is compared with the items and source passages the answer was built from. A sentence is *backed* when one source passage has at least 60% of its content words. Sentences that no source backs are listed as **no source says this**, so you know what to check. This check needs no model.

Extracted knowledge is checked in the same way when it's created: see [unsupported claims](knowledge-health.md#unsupported-claims).

## API

`POST /knowledge/assistant` with `{"question": "…"}` returns `plan` (agent, label, why) and `steps` (agent, label, why, ok, ms, and `result` or `error`).
