"""
Knowledge health: is what the knowledge base says still true?

An item's lifecycle status (services/lineage.py) says whether it was replaced.
Its health says whether it may have gone out of date while still current. It
is never derived from age alone, only from evidence, recorded as signals on
the item:

  source_changed       the source it was extracted from changed after it was accepted
  removed_from_source  the source changed and no longer contains it
  dependency_changed   an item it builds on changed: the decision it amends was reversed
                       or superseded, the decision that mitigates it (a risk) was, or
                       the risk it mitigates happened
  flagged              someone marked it as possibly outdated, with a reason

plus an optional review date set by a person. The resulting state:

  potentially_stale    at least one signal
  needs_review         no signal, but the review date is within REVIEW_SOON_DAYS or past
  ok                   neither

Someone confirming the item ("still valid") clears its signals and may set the
next review date. Items that are no longer current (superseded, reversed,
rejected, deprecated, closed) aren't asked about: their status already says it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.arcadedb import ArcadeSession
from app.repositories import ItemEventRepository, KnowledgeItemRepository, LineageRepository

Row = Dict[str, Any]

SIGNAL_KINDS = ("source_changed", "removed_from_source", "dependency_changed", "flagged")
REVIEW_SOON_DAYS = 14
# statuses that already say an item no longer holds
RETIRED_STATUSES = ["superseded", "reversed", "rejected", "deprecated", "closed"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> date:
    return datetime.now(timezone.utc).date()


def health_of(item: Row, today: Optional[date] = None) -> Row:
    """The item's health as the API reports it."""
    today = today or _today()
    signals = item.get("health_signals") or []
    due = item.get("review_due")
    if signals:
        state = "potentially_stale"
    elif due and due <= (today + timedelta(days=REVIEW_SOON_DAYS)).isoformat():
        state = "needs_review"
    else:
        state = "ok"
    return {
        "state": state,
        "signals": signals,
        "review_due": due,
        "overdue": bool(due and due < today.isoformat()),
        "verified_at": item.get("verified_at"),
        "verified_by": item.get("verified_by"),
    }


async def add_signal(
    session: ArcadeSession, user_id: str, item: Row, kind: str, detail: Optional[Row] = None,
    actor: Optional[str] = None,
) -> bool:
    """
    Record a signal on the item and in its history. A second open signal of the
    same kind about the same thing isn't added again. Returns whether it was added.
    No commit.
    """
    detail = detail or {}
    signals = list(item.get("health_signals") or [])
    about = detail.get("item_id")
    if any(s.get("kind") == kind and s.get("item_id") == about for s in signals):
        return False
    signal = {"kind": kind, "at": _now(), **detail}
    if actor:
        signal["actor"] = actor
    signals.append(signal)
    item["health_signals"] = signals
    await KnowledgeItemRepository(session).update(user_id, item["id"], {"health_signals": signals})
    await ItemEventRepository(session).add(user_id, item["id"], "health_flagged", {"kind": kind, **detail},
                                           actor=actor)
    return True


async def flag(session: ArcadeSession, user_id: str, item: Row, note: str, actor: Optional[str]) -> Row:
    """Someone marks the item as possibly outdated. Commits."""
    # each flag is its own signal: two people can flag for two reasons
    signals = list(item.get("health_signals") or [])
    signals.append({"kind": "flagged", "at": _now(), "note": note, "actor": actor})
    await KnowledgeItemRepository(session).update(user_id, item["id"], {"health_signals": signals})
    await ItemEventRepository(session).add(user_id, item["id"], "health_flagged", {"kind": "flagged", "note": note},
                                           actor=actor)
    await session.commit()
    return await KnowledgeItemRepository(session).get_owned(user_id, item["id"])


async def verify(
    session: ArcadeSession, user_id: str, item: Row, note: str, actor: Optional[str],
    review_due: Optional[str] = None,
) -> Row:
    """Someone confirms the item still holds: clears its signals, optionally sets the next review date. Commits."""
    fields: Row = {"health_signals": None, "verified_at": _now(), "verified_by": actor, "review_due": review_due}
    await KnowledgeItemRepository(session).update(user_id, item["id"], fields)
    await ItemEventRepository(session).add(
        user_id, item["id"], "verified",
        {"note": note, "cleared": [s.get("kind") for s in item.get("health_signals") or []],
         "review_due": review_due},
        actor=actor)
    await session.commit()
    return await KnowledgeItemRepository(session).get_owned(user_id, item["id"])


async def set_review_date(
    session: ArcadeSession, user_id: str, item: Row, review_due: Optional[str], actor: Optional[str],
) -> Row:
    """Set (or clear, with None) the date by which the item should be looked at again. Commits."""
    await KnowledgeItemRepository(session).update(user_id, item["id"], {"review_due": review_due})
    await ItemEventRepository(session).add(user_id, item["id"], "review_date_set", {"review_due": review_due},
                                           actor=actor)
    await session.commit()
    return await KnowledgeItemRepository(session).get_owned(user_id, item["id"])


# ── signals raised by the system ────────────────────────────────────────────

async def worth_keeping(session: ArcadeSession, user_id: str, item_ids: List[str]) -> List[str]:
    """
    Of items about to disappear from a changed source, the ones someone relied
    on: accepted in review, or part of the decision history. These are kept
    and flagged instead of deleted, so an edited ADR can't silently erase a
    decision and its history.
    """
    if not item_ids:
        return []
    accepted = await session.query(
        "SELECT id FROM KnowledgeItem WHERE user_id = :u AND id IN :ids AND review_status = 'accepted'",
        {"u": user_id, "ids": item_ids},
    )
    linked = {end for e in await LineageRepository(session).list(user_id) for end in (e["from"], e["to"])}
    keep = {r["id"] for r in accepted} | linked
    return [i for i in item_ids if i in keep]


async def source_changed(
    session: ArcadeSession, user_id: str, artifact: Row, kept_ids: Iterable[str], removed_ids: Iterable[str],
) -> None:
    """
    After a source's content changed: flag its accepted items (they may no
    longer say what the source says) and the kept items it no longer contains.
    Pending items aren't flagged: they still wait for review. No commit.
    """
    repo = KnowledgeItemRepository(session)
    detail = {"source_id": artifact["id"], "source_title": artifact.get("title")}
    for item in await repo.get_many(user_id, list(removed_ids)):
        await add_signal(session, user_id, item, "removed_from_source", detail)
    for item in await repo.get_many(user_id, list(kept_ids)):
        if item.get("review_status") == "accepted":
            await add_signal(session, user_id, item, "source_changed", detail)


# (changed item's new status, edge kind, which end of the edge is affected, why)
_PROPAGATION = [
    ("reversed", "amends", "from", "The decision it amends was reversed"),
    ("superseded", "amends", "from", "The decision it amends was superseded"),
    ("reversed", "mitigates", "to", "A decision that mitigates it was reversed"),
    ("superseded", "mitigates", "to", "A decision that mitigates it was superseded"),
    ("materialized", "mitigates", "from", "The risk it mitigates happened"),
]


async def dependencies_changed(
    session: ArcadeSession, user_id: str, changes: List[Row], edges: List[Row], titles: Dict[str, str],
) -> None:
    """
    Status changes (from lineage.refresh_statuses) that put other items in
    doubt: an amendment of a decision that no longer holds, a risk whose
    mitigation was dropped, a mitigation whose risk happened anyway. No commit.
    """
    repo = KnowledgeItemRepository(session)
    for change in changes:
        if not change.get("from"):   # an item's first status isn't a change
            continue
        for status, kind, affected_end, reason in _PROPAGATION:
            if change["to"] != status:
                continue
            changed_end = "to" if affected_end == "from" else "from"
            for edge in edges:
                if edge["kind"] != kind or edge[changed_end] != change["id"]:
                    continue
                item = await repo.get_owned(user_id, edge[affected_end])
                if item and item.get("status") not in RETIRED_STATUSES:
                    await add_signal(session, user_id, item, "dependency_changed",
                                     {"reason": reason, "item_id": change["id"],
                                      "item_title": titles.get(change["id"])})


# ── the attention list ──────────────────────────────────────────────────────

async def attention(session: ArcadeSession, user_id: str, limit: int, offset: int) -> Row:
    """
    Current items that need a look: potentially stale first (most signals
    first), then review dates, soonest first. `counts` covers all of them.
    """
    soon = (_today() + timedelta(days=REVIEW_SOON_DAYS)).isoformat()
    rows = await session.query(
        "SELECT id FROM KnowledgeItem WHERE user_id = :u AND review_status <> 'rejected' "
        "AND (status IS NULL OR status NOT IN :retired) "
        "AND (health_signals IS NOT NULL OR (review_due IS NOT NULL AND review_due <= :soon))",
        {"u": user_id, "retired": RETIRED_STATUSES, "soon": soon},
    )
    items = await KnowledgeItemRepository(session).get_many(user_id, [r["id"] for r in rows])
    today = _today()
    scored = [(item, health_of(item, today)) for item in items]
    scored = [(i, h) for i, h in scored if h["state"] != "ok"]
    scored.sort(key=lambda pair: (pair[1]["state"] != "potentially_stale", -len(pair[1]["signals"]),
                                  pair[1]["review_due"] or "", pair[0].get("title") or ""))
    counts = {"potentially_stale": 0, "needs_review": 0, "overdue": 0}
    for _, h in scored:
        counts[h["state"]] += 1
        counts["overdue"] += h["overdue"]
    return {"items": [i for i, _ in scored[offset:offset + limit]], "total": len(scored), "counts": counts}
