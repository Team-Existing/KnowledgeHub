"""
Commitments and outcomes: what decisions set in motion, and whether they worked.

Commitments are action items. Each has a lifecycle status (open, in_progress,
done, dropped; declared like a decision's, see services/lineage.py), an owner
and a due date, and usually an `implements` link to the decision it carries
out. The owner and due date start from what extraction found (details.who /
details.due) and can be set by hand; a due date written as words ("by Friday")
stays as text until someone sets a real date. A commitment is overdue when its
due date has passed and it's neither done nor dropped.

Outcomes belong to decisions. A team records what it expects a decision to
achieve and when to check, then a verdict (achieved, partly, not_achieved)
with a note. The evidence shown with it is what the graph already knows: the
decision's commitments and how many are done, the lessons learned from it, and
the risks it mitigates. A decision whose outcome wasn't achieved is flagged on
the Health page, since what it promised no longer holds.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from app.arcadedb import ArcadeSession
from app.repositories import ItemEventRepository, KnowledgeItemRepository, LineageRepository
from app.services import health

Row = Dict[str, Any]

OPEN, IN_PROGRESS, DONE, DROPPED = "open", "in_progress", "done", "dropped"
COMMITMENT_STATUSES = [OPEN, IN_PROGRESS, DONE, DROPPED]
FINISHED = {DONE, DROPPED}
VERDICTS = ("achieved", "partly", "not_achieved")


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_date(value: Any) -> Optional[str]:
    """An ISO date (YYYY-MM-DD) if the text starts with one, else None."""
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def commitment_view(item: Row, today: Optional[date] = None) -> Row:
    """Owner, due date and whether it's overdue: the hand-set values, else what extraction found."""
    details = item.get("details") or {}
    due_date = item.get("due_date") or parse_date(details.get("due"))
    status = item.get("status") or OPEN
    return {
        "owner": item.get("owner") or details.get("who") or None,
        "due_date": due_date,
        "due_text": None if due_date else (details.get("due") or None),   # "by Friday": shown, not computed with
        "overdue": bool(due_date and status not in FINISHED and due_date < (today or _today()).isoformat()),
    }


def outcome_view(item: Row, today: Optional[date] = None) -> Row:
    check_by = item.get("outcome_check_by")
    verdict = item.get("outcome")
    return {
        "expected": item.get("expected_outcome"),
        "check_by": check_by,
        "due": bool(check_by and not verdict and check_by <= (today or _today()).isoformat()),
        "verdict": verdict,
        "note": item.get("outcome_note"),
        "recorded_at": item.get("outcome_at"),
        "recorded_by": item.get("outcome_by"),
    }


def context_details(item: Row) -> Row:
    """What GraphRAG adds to an item's details so answers can say who owns what and whether it worked."""
    out: Row = {}
    if item.get("type") == "action-item":
        view = commitment_view(item)
        if view["owner"]:
            out["owner"] = view["owner"]
        if view["due_date"]:
            out["due"] = view["due_date"] + (" (overdue)" if view["overdue"] else "")
    if item.get("type") == "decision":
        if item.get("expected_outcome"):
            out["expected outcome"] = item["expected_outcome"]
        if item.get("outcome"):
            verdict = item["outcome"].replace("_", " ")
            out["outcome"] = verdict + (f": {item['outcome_note']}" if item.get("outcome_note") else "")
    return out


# ── changes ─────────────────────────────────────────────────────────────────

class TrackingError(ValueError):
    pass


async def update_commitment(session: ArcadeSession, user_id: str, item: Row, fields: Row,
                            actor: Optional[str]) -> Row:
    """Set the owner and/or due date (None clears). Commits."""
    if item["type"] != "action-item":
        raise TrackingError("Only action items are commitments")
    changes = {k: v for k, v in fields.items() if item.get(k) != v}
    if changes:
        await KnowledgeItemRepository(session).update(user_id, item["id"], changes)
        await ItemEventRepository(session).add(user_id, item["id"], "commitment_updated", changes, actor=actor)
        await session.commit()
    return await KnowledgeItemRepository(session).get_owned(user_id, item["id"])


async def set_expectation(session: ArcadeSession, user_id: str, item: Row, expected: Optional[str],
                          check_by: Optional[str], actor: Optional[str]) -> Row:
    """What the decision should achieve, and when to check. Commits."""
    if item["type"] != "decision":
        raise TrackingError("Only decisions have outcomes")
    await KnowledgeItemRepository(session).update(user_id, item["id"], {
        "expected_outcome": expected or None, "outcome_check_by": check_by})
    await ItemEventRepository(session).add(user_id, item["id"], "outcome_expected",
                                           {"expected": expected, "check_by": check_by}, actor=actor)
    await session.commit()
    return await KnowledgeItemRepository(session).get_owned(user_id, item["id"])


async def record_outcome(session: ArcadeSession, user_id: str, item: Row, verdict: Optional[str], note: str,
                         actor: Optional[str]) -> Row:
    """The verdict (None withdraws it). A missed outcome is flagged on the Health page. Commits."""
    if item["type"] != "decision":
        raise TrackingError("Only decisions have outcomes")
    if verdict is not None and verdict not in VERDICTS:
        raise TrackingError(f"The verdict must be one of: {', '.join(VERDICTS)}")
    await KnowledgeItemRepository(session).update(user_id, item["id"], {
        "outcome": verdict, "outcome_note": note or None,
        "outcome_at": _now() if verdict else None, "outcome_by": actor if verdict else None})
    await ItemEventRepository(session).add(user_id, item["id"], "outcome_recorded",
                                           {"verdict": verdict, "note": note}, actor=actor)
    if verdict == "not_achieved":
        await health.add_signal(session, user_id, item, "outcome_not_achieved", {"note": note} if note else {},
                                actor=actor)
    await session.commit()
    return await KnowledgeItemRepository(session).get_owned(user_id, item["id"])


# ── views ───────────────────────────────────────────────────────────────────

def _brief(item: Row) -> Row:
    return {"id": item["id"], "title": item.get("title"), "status": item.get("status")}


async def commitments(session: ArcadeSession, user_id: str, show: str, limit: int, offset: int) -> Row:
    """
    Commitments, most pressing first: overdue, then by due date, then those without
    one. `show`: all | open (not finished) | overdue | done (finished). Counts cover all.
    """
    from app.serializers import item_dict   # it uses this module's views
    repo = KnowledgeItemRepository(session)
    rows = await repo.list(user_id, types=["action-item"], exclude_rejected=True)
    edges = await LineageRepository(session).list(user_id)
    implements = {e["from"]: e["to"] for e in edges if e["kind"] == "implements"}
    decisions = {d["id"]: d for d in await repo.get_many(user_id, sorted(set(implements.values())))}
    today = _today()

    entries, counts = [], {s: 0 for s in COMMITMENT_STATUSES} | {"overdue": 0}
    for item in rows:
        view = commitment_view(item, today)
        status = item.get("status") or OPEN
        counts[status] = counts.get(status, 0) + 1
        counts["overdue"] += view["overdue"]
        if (show == "open" and status in FINISHED) or (show == "overdue" and not view["overdue"]) \
                or (show == "done" and status not in FINISHED):
            continue
        decision = decisions.get(implements.get(item["id"], ""))
        entries.append({**item_dict(item), **view, "status": status,
                        "decision": _brief(decision) if decision else None})
    entries.sort(key=lambda e: (not e["overdue"], e["status"] in FINISHED, e["due_date"] or "9999", e["title"] or ""))
    return {"items": entries[offset:offset + limit], "total": len(entries), "counts": counts}


async def outcomes(session: ArcadeSession, user_id: str) -> Row:
    """
    Decisions with an expected outcome or a verdict: those waiting for a verdict
    (due ones first), then recorded ones, newest first. Each with its evidence.
    """
    from app.serializers import item_dict   # it uses this module's views
    repo = KnowledgeItemRepository(session)
    rows = [d for d in await repo.list(user_id, types=["decision"], exclude_rejected=True)
            if d.get("expected_outcome") or d.get("outcome")]
    edges = await LineageRepository(session).list(user_id)
    related_ids = {e["from"] for e in edges if e["kind"] in ("implements", "learned_from")} | \
                  {e["to"] for e in edges if e["kind"] == "mitigates"}
    related = {i["id"]: i for i in await repo.get_many(user_id, sorted(related_ids))}
    today = _today()

    awaiting, recorded = [], []
    counts = {v: 0 for v in VERDICTS} | {"awaiting": 0, "due": 0}
    for d in rows:
        commits = [related[e["from"]] for e in edges
                   if e["kind"] == "implements" and e["to"] == d["id"] and e["from"] in related]
        entry = {
            **item_dict(d), "outcome": outcome_view(d, today),
            "commitments": {"total": len(commits),
                            "done": sum((c.get("status") or OPEN) == DONE for c in commits),
                            "overdue": sum(commitment_view(c, today)["overdue"] for c in commits)},
            "lessons": [_brief(related[e["from"]]) for e in edges
                        if e["kind"] == "learned_from" and e["to"] == d["id"] and e["from"] in related],
            "mitigates": [_brief(related[e["to"]]) for e in edges
                          if e["kind"] == "mitigates" and e["from"] == d["id"] and e["to"] in related],
        }
        if d.get("outcome"):
            counts[d["outcome"]] += 1
            recorded.append(entry)
        else:
            counts["awaiting"] += 1
            counts["due"] += entry["outcome"]["due"]
            awaiting.append(entry)
    awaiting.sort(key=lambda e: (not e["outcome"]["due"], e["outcome"]["check_by"] or "9999", e["title"] or ""))
    recorded.sort(key=lambda e: e["outcome"]["recorded_at"] or "", reverse=True)
    return {"awaiting": awaiting, "recorded": recorded, "counts": counts}
