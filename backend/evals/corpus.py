"""
Evaluation corpus: fictional sources with the knowledge each one holds (the
answer key), and questions with the facts a good answer must contain.

A gold item matches an extracted item of the same type when every keyword
group matches the item's title and details (a group is a set of alternatives,
matched case-insensitively as substrings). Keep groups short and distinctive:
they test that the item is *about* the right thing, not its exact wording.
"""
from __future__ import annotations

from typing import Any, Dict, List

# ── sources ─────────────────────────────────────────────────────────────────
# kind: "transcript" | "email" | "slack" (POST /knowledge/artifacts/transcript),
#       "document" (upload, LLM extraction) | "text" (pasted, rule-based)

SOURCES: List[Dict[str, Any]] = [
    {
        "title": "Platform sync, 3 March",
        "kind": "transcript",
        "content": """\
Priya: Morning all. First item, the search latency problem. Tomas, where are we?
Tomas: p95 is at 900 milliseconds on the search endpoint. Most of it is the database round trips.
Wen: I benchmarked a cache in front of it. Sixty-second cache cuts p95 to about 120.
Priya: Any objections to caching search results for sixty seconds? No? Then we're doing that. Decided.
Tomas: The catch is people will see stale results right after they edit something.
Priya: That's acceptable for now, but let's call it out. Wen, can you add cache hit-rate metrics to the dashboard by Friday?
Wen: Yes, I'll have the hit-rate panel up by Friday.
Priya: Second item: the on-call rotation. Two people burned out last month.
Tomas: I'd like to move from two-week shifts to one-week shifts.
Priya: Agreed, one-week on-call shifts starting in April.
Wen: Risk though: with shorter shifts the handovers double, and we lose context in handovers.
Priya: Tomas, please write a handover checklist before April.
""",
        "gold": [
            {"type": "decision", "keywords": [["cache", "caching"], ["search"], ["60", "sixty"]]},
            {"type": "decision", "keywords": [["on-call", "on call", "oncall"], ["one-week", "one week", "1-week", "weekly", "1 week"]]},
            {"type": "action-item", "keywords": [["hit-rate", "hit rate", "metrics"], ["dashboard", "panel"]]},
            {"type": "action-item", "keywords": [["handover"], ["checklist"]]},
            {"type": "risk", "keywords": [["stale"]]},
            {"type": "risk", "keywords": [["handover"], ["context", "double", "more"]]},
        ],
    },
    {
        "title": "Re: Billing database choice",
        "kind": "email",
        "content": """\
From: Ana Lopez
To: platform-team
Subject: Re: Billing database choice

Hi all,

Following Tuesday's review we have decided to keep billing on PostgreSQL and add a read replica
for the reporting queries, instead of moving billing to the document store. The reporting load was
the only real reason to move, and a replica solves it with far less risk.

Marco will set up the read replica and the replication-lag alert by the end of the month.

One concern: replication lag could show finance slightly out-of-date numbers during month-end close.
We'll pause heavy reports during the close window if that happens.

Thanks,
Ana

> On Mon, Marco wrote:
> Should we just move billing to the document store?
""",
        "gold": [
            {"type": "decision", "keywords": [["postgres"], ["replica"]]},
            {"type": "action-item", "keywords": [["replica"], ["set up", "setup", "configure", "alert"]]},
            {"type": "risk", "keywords": [["lag"]]},
        ],
    },
    {
        "title": "#release-train thread",
        "kind": "slack",
        "content": """\
Jonas [09:12 AM] we keep shipping broken builds on Fridays
Mia [09:13 AM] proposal: no production deploys after 2pm on Fridays
Jonas [09:15 AM] +1
Mia [09:20 AM] ok, agreed by the release council: freeze production deploys after 2pm on Fridays, effective next week
Jonas [09:21 AM] I'll update the deploy bot to block Friday afternoon deploys
Mia [09:23 AM] worry: urgent security fixes might get stuck behind the freeze. we need an exception path
""",
        "gold": [
            {"type": "decision", "keywords": [["friday"], ["2pm", "2 pm", "14:00", "afternoon"], ["deploy"]]},
            {"type": "action-item", "keywords": [["bot"], ["block", "update"]]},
            {"type": "risk", "keywords": [["security", "urgent", "hotfix"]]},
        ],
    },
    {
        "title": "Postmortem: login outage on 12 May",
        "kind": "document",
        "content": """\
# Postmortem: login outage on 12 May

## Summary

For 47 minutes on 12 May no one could sign in. The authentication service exhausted its database
connection pool after a configuration change lowered the pool size from 50 to 5.

## Timeline

09:02 a configuration change went out with the weekly release. 09:10 sign-in errors started.
09:49 the change was rolled back and sign-ins recovered.

## What we learned

Lesson learned: configuration changes need the same review as code changes. The pool size change
was made directly in the environment and never reviewed.

Lesson learned: our alerting watched error rates but not connection-pool saturation, so we found out
from users instead of from monitoring.

## Decisions

We decided that every configuration change goes through a pull request with one reviewer, the same as
code. We also decided to alert when the connection pool is more than 80% used for five minutes.

## Follow-ups

Action: Lena adds a connection-pool saturation alert by 20 May.
Action: Omar moves the environment configuration into the infrastructure repository.

## Best practices

Always roll back first and investigate second when a release breaks sign-in.

## Risks

Other services still read configuration from unreviewed environment variables; the same failure could
happen there.
""",
        "gold": [
            {"type": "lesson", "keywords": [["config"], ["review"]]},
            {"type": "lesson", "keywords": [["alert", "monitor"], ["pool", "saturation"]]},
            {"type": "decision", "keywords": [["config"], ["pull request", "reviewer", "review"]]},
            {"type": "decision", "keywords": [["alert"], ["80", "pool"]]},
            {"type": "action-item", "keywords": [["alert"], ["saturation", "pool"]]},
            {"type": "action-item", "keywords": [["config"], ["repo", "repository", "infrastructure"]]},
            {"type": "best-practice", "keywords": [["roll back", "rollback", "roll-back"]]},
            {"type": "risk", "keywords": [["environment variable", "env var", "other services", "unreviewed"]]},
        ],
    },
    {
        "title": "Runbook: rotating the TLS certificate",
        "kind": "document",
        "content": """\
# Runbook: rotating the TLS certificate

The public certificate expires every 90 days. Rotate it at least a week before expiry.

## How to rotate the certificate

1. Request a new certificate from the internal CA with the `certctl request` command.
2. Upload the certificate and key to the secrets vault under `edge/tls`.
3. Run the `edge-reload` pipeline so the load balancers pick up the new certificate.
4. Check the expiry date at https://status.example.internal/tls.

## Notes

Best practice: never paste private keys into chat or tickets; share the vault path instead.

Risk: if the reload pipeline fails silently, the old certificate keeps serving until it expires and the
site goes down. Always check the expiry page after a rotation.
""",
        "gold": [
            {"type": "how-to", "keywords": [["certificate", "cert", "tls"], ["rotat", "renew"]]},
            {"type": "best-practice", "keywords": [["private key", "keys"], ["chat", "ticket", "vault"]]},
            {"type": "risk", "keywords": [["reload", "pipeline"], ["silent", "expire", "old certificate", "down"]]},
        ],
    },
    {
        "title": "ADR-0007: Adopt trunk-based development",
        "kind": "document",
        "content": """\
# ADR-0007: Adopt trunk-based development

Status: Accepted. Supersedes ADR-0004 (long-lived release branches).

## Context

Release branches lived for up to six weeks. Merging them back caused painful conflicts, and two
releases slipped because of merge problems.

## Decision

We will use trunk-based development: short-lived feature branches merged to main within two days,
with unfinished work hidden behind feature flags.

## Consequences

Risk: feature flags pile up and nobody removes them. Each flag must have an owner and a removal date.

Lesson learned: long-lived branches hid integration problems until release week.
""",
        "gold": [
            {"type": "decision", "keywords": [["trunk"]]},
            {"type": "risk", "keywords": [["flag"]]},
            {"type": "lesson", "keywords": [["long-lived", "long lived", "branch"]]},
        ],
    },
    {
        "title": "Q3 retrospective notes",
        "kind": "text",
        "content": (
            "Q3 retrospective. We decided to hold design reviews before any work over five days. "
            "Lesson learned: estimates without a design review were off by 2x on average. "
            "Risk: design reviews could become a bottleneck if only two senior engineers run them. "
            "Best practice: write the design doc before the review meeting, not during it."
        ),
        "gold": [
            {"type": "decision", "keywords": [["design review"]]},
            {"type": "lesson", "keywords": [["estimate"]]},
            {"type": "risk", "keywords": [["bottleneck"]]},
            {"type": "best-practice", "keywords": [["design doc"]]},
        ],
    },
]

# ── questions ───────────────────────────────────────────────────────────────
# facts: groups of alternatives the answer must contain (all groups, any alternative);
# sources: titles of the sources a correct answer draws on (at least one must be cited);
# answerable=False: the knowledge base has nothing on it; the answer must say so.

QUESTIONS: List[Dict[str, Any]] = [
    {"q": "How long are search results cached, and why?", "facts": [["60", "sixty"], ["latency", "p95", "fast", "900"]],
     "sources": ["Platform sync, 3 March"]},
    {"q": "What is the downside of caching search results?", "facts": [["stale"]],
     "sources": ["Platform sync, 3 March"]},
    {"q": "Who is adding cache metrics to the dashboard, and by when?", "facts": [["wen"], ["friday"]],
     "sources": ["Platform sync, 3 March"]},
    {"q": "How long are on-call shifts now?", "facts": [["one week", "one-week", "1 week", "weekly", "1-week"]],
     "sources": ["Platform sync, 3 March"]},
    {"q": "Which database does billing use, and how are reporting queries handled?",
     "facts": [["postgres"], ["replica"]], "sources": ["Re: Billing database choice"]},
    {"q": "Why didn't we move billing to the document store?", "facts": [["replica"], ["risk", "reporting"]],
     "sources": ["Re: Billing database choice"]},
    {"q": "What risk does the billing read replica bring?", "facts": [["lag"]],
     "sources": ["Re: Billing database choice"]},
    {"q": "When are production deploys frozen?", "facts": [["friday"], ["2pm", "2 pm", "14:00", "afternoon"]],
     "sources": ["#release-train thread"]},
    {"q": "What concern was raised about the deploy freeze?", "facts": [["security", "urgent", "hotfix"]],
     "sources": ["#release-train thread"]},
    {"q": "What caused the login outage in May?", "facts": [["pool"], ["config"]],
     "sources": ["Postmortem: login outage on 12 May"]},
    {"q": "What did we learn from the login outage?", "facts": [["review"], ["alert", "monitor"]],
     "sources": ["Postmortem: login outage on 12 May"]},
    {"q": "How do configuration changes get reviewed now?", "facts": [["pull request"], ["reviewer", "one review"]],
     "sources": ["Postmortem: login outage on 12 May"]},
    {"q": "What should you do first when a release breaks sign-in?", "facts": [["roll back", "rollback", "roll-back"]],
     "sources": ["Postmortem: login outage on 12 May"]},
    {"q": "How do I rotate the TLS certificate?", "facts": [["vault"], ["edge-reload", "reload"]],
     "sources": ["Runbook: rotating the TLS certificate"]},
    {"q": "How should private keys be shared?", "facts": [["vault"]],
     "sources": ["Runbook: rotating the TLS certificate"]},
    {"q": "Which branching strategy do we use, and what did it replace?",
     "facts": [["trunk"], ["release branch", "long-lived", "adr-0004"]],
     "sources": ["ADR-0007: Adopt trunk-based development"]},
    {"q": "What is the risk with feature flags?", "facts": [["owner", "remov", "pile", "nobody"]],
     "sources": ["ADR-0007: Adopt trunk-based development"]},
    {"q": "When do we hold design reviews?", "facts": [["five days", "5 days"]],
     "sources": ["Q3 retrospective notes"]},
    {"q": "What risks have been raised across the team?", "facts": [["stale", "lag", "flag", "bottleneck", "handover", "security"]],
     "sources": ["Platform sync, 3 March", "Re: Billing database choice", "ADR-0007: Adopt trunk-based development",
                 "Q3 retrospective notes", "#release-train thread", "Postmortem: login outage on 12 May"]},
    # nothing in the knowledge base
    {"q": "What is our parental leave policy?", "answerable": False},
    {"q": "Which cloud region hosts the analytics cluster?", "answerable": False},
    {"q": "What did we decide about Kubernetes autoscaling?", "answerable": False},
]

REFUSAL_MARKERS = (
    "doesn't cover", "does not cover",
    "no information", "not contain", "doesn't contain", "does not contain", "no relevant", "not mention",
    "don't have", "do not have", "unable to", "cannot answer", "can't answer", "not enough information",
    "no mention", "not provided", "isn't covered", "is not covered", "not covered", "no details",
    "does not provide", "doesn't provide", "not specified", "no record", "not found", "couldn't find",
    "could not find", "not sufficiently relevant", "does not include", "doesn't include", "no data",
)
