"""
The assistant: one question, the agents that can answer it, and a check of what they say.

  1. plan: rules pick the agents from the question's wording and the systems it
     names. No model call, so the plan is fast, the same every time, and each
     step says why it was chosen.
       research      GraphRAG: an answer from the record, with citations
       impact        a proposed change: the decisions, systems, risks and lessons it touches
       learning      a system's map; what was abandoned; recurring mistakes; controversy
       watchtower    what may be out of date, overdue commitments, outcomes to judge
  2. run: each step in turn. A step that fails is reported and the others still run.
  3. verify: each sentence of the research answer is compared with the sources it
     drew on (services/grounding.py, text only). Sentences none of them back are
     marked, so a reader knows what to check before relying on it.

Agents read only: the assistant never changes the record.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List

from app.arcadedb import ArcadeSession
from app.repositories import GraphStore, SystemRepository
from app.services import grounding, health, impact, learning, tracking
from app.services.graphrag import _item_text, graphrag_query, is_no_answer

logger = logging.getLogger(__name__)

Row = Dict[str, Any]

AGENTS = {
    "research": "Research & answer",
    "impact": "Impact",
    "learning": "Learning",
    "watchtower": "Watchtower",
    "verification": "Verification",
}
SHOW = 5                  # entries per list in a step's result; the pages have the rest
BACKED = 0.6              # share of a sentence's content words one source passage needs

_CHANGE = re.compile(r"\b(what if|if we|should we|can we|could we|plan(ning)? to|propos\w*|migrat\w*|replac\w*|"
                     r"switch\w*|mov(e|ing) (to|from|off)|remov\w*|deprecat\w*|introduc\w*|adopt\w*|upgrad\w*|"
                     r"rewrit\w*|retir\w*|split\w*|merg\w*)\b", re.I)
_ABANDONED = re.compile(r"\b(tried|abandon\w*|gave up|given up|didn'?t work|rejected|instead of|alternatives?)\b", re.I)
_MISTAKES = re.compile(r"\b(mistakes?|incidents?|outages?|postmortems?|keep happening|recurring|repeated|lessons?)\b", re.I)
_CONTROVERSIAL = re.compile(r"\b(controvers\w*|disagree\w*|debat\w*|contentious|back and forth|flip.?flop\w*)\b", re.I)
_UNDERSTAND = re.compile(r"\b(before (chang|modif|touch|work)\w*|understand|onboard\w*|new to|history of|"
                         r"why does|why do|why is|why are|look(s)? (this|that) way|came to be)\b", re.I)
_WATCH = re.compile(r"\b(out of date|stale|outdated|attention|overdue|due|reviews?|commitments?|promis\w*|"
                    r"follow.?ups?|pending|still (true|valid|hold\w*)|outcomes?)\b", re.I)


def _step(agent: str, why: str, **args: Any) -> Row:
    return {"agent": agent, "label": AGENTS[agent], "why": why, "args": args}


def plan(question: str, systems: List[Row]) -> List[Row]:
    """The steps for a question, in order, each with the reason it was chosen."""
    named = [s for s in systems if impact.mentions(question, s)]
    names = ", ".join(s["name"] for s in named)
    steps: List[Row] = []
    if _CHANGE.search(question):
        steps.append(_step("impact", "it describes a change: what the record says about it", proposal=question))
    if named and _UNDERSTAND.search(question):
        steps.append(_step("learning", f"it asks what to understand about {names}", view="map",
                           system_ids=[s["id"] for s in named]))
    for view, pattern, why in (("abandoned", _ABANDONED, "it asks what was tried or not chosen"),
                               ("mistakes", _MISTAKES, "it asks about mistakes and lessons"),
                               ("controversial", _CONTROVERSIAL, "it asks about disagreement")):
        if pattern.search(question):
            steps.append(_step("learning", why, view=view))
    if _WATCH.search(question):
        steps.append(_step("watchtower", "it asks what needs attention or follow-up"))
    # an answer in words, unless the question only asks for one of the lists above
    if not steps or re.match(r"\s*(why|how|who|when|what|which|is|are|does|did|can|should)\b", question, re.I):
        steps.append(_step("research", "an answer from the record, with citations" if steps
                           else "no specialised agent fits: answer from the record"))
        steps.append(_step("verification", "check the answer against the sources it drew on"))
    return steps


# ── agents ──────────────────────────────────────────────────────────────────

def _brief(entry: Row) -> Row:
    return {k: entry.get(k) for k in ("id", "title", "type", "status") if entry.get(k) is not None}


async def _research(session: ArcadeSession, user_id: str, question: str, state: Row) -> Row:
    result = await graphrag_query(question=question, store=GraphStore(session), user_id=user_id)
    state["research"] = result
    return {"answer": result.get("answer", ""), "citations": result.get("citations", []),
            "no_answer": is_no_answer(result.get("answer", ""))}


async def _impact(session: ArcadeSession, user_id: str, proposal: str) -> Row:
    report = await impact.analyze(session, user_id, proposal, [])
    return {"counts": report["counts"], "llm_used": report["llm_used"],
            "systems": [{"id": s.get("id"), "name": s.get("name"), "reason": s.get("reason")}
                        for s in report["systems"][:SHOW]],
            "decisions": [{**_brief(d), "relation": d.get("relation"), "why": d.get("why"),
                           "reason": d.get("relation_reason")} for d in report["decisions"][:SHOW]],
            "risks": [_brief(r) for r in report["risks"][:SHOW]],
            "lessons": [_brief(l) for l in report["lessons"][:SHOW]],
            "commitments": [_brief(c) for c in report["commitments"][:SHOW]]}


async def _learning(session: ArcadeSession, user_id: str, view: str, system_ids: List[str] = ()) -> Row:
    if view == "map":
        found = await learning.knowledge_map(session, user_id, list(system_ids))
        return {"view": view, "systems": [{
            "system": {"id": m["system"]["id"], "name": m["system"]["name"]},
            "current": [{**_brief(d), "why": d.get("why"), "alternatives": d.get("alternatives") or []}
                        for d in m["current"][:SHOW]],
            "risks": [_brief(r) for r in m["risks"][:SHOW]],
            "lessons": [_brief(l) for l in m["lessons"][:SHOW]],
            "depends_on": m["depends_on"], "dependents": m["dependents"]} for m in found["systems"]]}
    if view == "abandoned":
        found = await learning.abandoned(session, user_id)
        return {"view": view, "decisions": [{**_brief(d), "how": d.get("how"),
                                             "replaced_by": d.get("replaced_by")} for d in found["decisions"][:SHOW]],
                "rejected_alternatives": found["rejected_alternatives"][:SHOW]}
    if view == "mistakes":
        found = await learning.mistakes(session, user_id)
        return {"view": view, "groups": [{"title": g["title"], "count": g["count"], "systems": g["systems"],
                                          "items": [_brief(i) for i in g["items"]]} for g in found["groups"][:SHOW]]}
    found = await learning.controversial(session, user_id)
    return {"view": view, "decisions": [{**_brief(d), "reasons": d["reasons"]} for d in found["decisions"][:SHOW]]}


async def _watchtower(session: ArcadeSession, user_id: str) -> Row:
    attention = await health.attention(session, user_id, limit=SHOW, offset=0)
    overdue = await tracking.commitments(session, user_id, "overdue", SHOW, 0)
    outcomes = await tracking.outcomes(session, user_id)
    due = [d for d in outcomes["awaiting"] if d["outcome"]["due"]]
    return {"attention": [{**_brief(i), "signals": len(i.get("health_signals") or [])} for i in attention["items"]],
            "attention_total": attention["total"],
            "overdue_commitments": [{**_brief(c), "owner": c.get("owner"), "due_date": c.get("due_date")}
                                    for c in overdue["items"]],
            "overdue_total": overdue["total"],
            "outcomes_due": [_brief(d) for d in due[:SHOW]], "outcomes_due_total": len(due)}


def verify_answer(answer: str, research: Row) -> Row:
    """Each sentence of the answer, with the source passage that backs it best (none: unbacked)."""
    sources = []
    for n in research.get("context_nodes") or []:
        text = " ".join(x for x in (_item_text(n), n.get("passage") or "") if x)
        sources.append({"id": n.get("id"), "title": n.get("title") or n.get("label"), "text": text})
    for s in research.get("summaries") or []:
        sources.append({"id": s.get("artifact_id"), "title": s.get("title"), "text": s.get("summary") or ""})
    sentences = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", answer or ""):
        text = re.sub(r"\[[^\]]*\]", "", raw).strip(" -*•")
        if len(grounding._words(text)) < 3:       # headings, "In short:" and the like
            continue
        best = max(((p[0][0], src) for src in sources if (p := grounding.best_passages(text, src["text"], keep=1))),
                   key=lambda t: t[0], default=(0.0, None))
        sentences.append({"text": text, "backed": best[0] >= BACKED, "score": best[0],
                          "source": {"id": best[1]["id"], "title": best[1]["title"]} if best[1] else None})
    backed = sum(s["backed"] for s in sentences)
    return {"sentences": sentences, "backed": backed, "unbacked": len(sentences) - backed}


async def run(session: ArcadeSession, user_id: str, question: str) -> Row:
    """{question, plan, steps: [{agent, label, why, ok, ms, result | error}]}"""
    systems = await SystemRepository(session).list(user_id)
    steps = plan(question, systems)
    state: Row = {}
    calls: Dict[str, Callable[..., Awaitable[Row]]] = {
        "research": lambda: _research(session, user_id, question, state),
        "impact": lambda proposal: _impact(session, user_id, proposal),
        "learning": lambda view, system_ids=(): _learning(session, user_id, view, system_ids),
        "watchtower": lambda: _watchtower(session, user_id),
    }
    done = []
    for step in steps:
        t0 = time.monotonic()
        out = {k: step[k] for k in ("agent", "label", "why")}
        try:
            if step["agent"] == "verification":
                research = state.get("research")
                if research is None or is_no_answer(research.get("answer", "")):
                    out.update(ok=True, result={"skipped": "there is no answer to check"})
                else:
                    out.update(ok=True, result=verify_answer(research["answer"], research))
            else:
                out.update(ok=True, result=await calls[step["agent"]](**step["args"]))
        except Exception as e:
            logger.exception("Assistant step %s failed", step["agent"])
            out.update(ok=False, error=str(e) or type(e).__name__)
        out["ms"] = int((time.monotonic() - t0) * 1000)
        done.append(out)
    return {"question": question, "plan": [{k: s[k] for k in ("agent", "label", "why")} for s in steps], "steps": done}
