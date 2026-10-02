"""
Decision tracking over time.

Items are linked newer -> older with EVOLVES edges of one of these kinds:

  supersedes    decision -> decision   the newer decision replaces the older one
  reverses      decision -> decision   the newer decision undoes the older one
  amends        decision -> decision   the newer decision changes part of the older one
  realizes      any      -> risk       evidence that the risk actually happened
  mitigates     any      -> risk       something that reduces or removes the risk
  learned_from  lesson   -> any        the lesson came out of that item (incident, decision, ...)

Decisions and risks carry a lifecycle `status`. It is derived, never edited
directly: incoming edges win (reverses > supersedes, realizes > mitigates),
then the user's `declared_status`, then the default (active / open). Every
change that could move a status calls refresh_statuses(), which stores the new
status and records a status_changed event — so the item history explains
itself.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional, Set

from app.arcadedb import ArcadeSession
from app.repositories import GraphStore, ItemEventRepository, KnowledgeItemRepository, LineageRepository
from app.serializers import item_dict

Row = Dict[str, Any]

# kind -> (allowed source types, allowed target types); None = any type
LINK_KINDS: Dict[str, tuple[Optional[Set[str]], Optional[Set[str]]]] = {
    "supersedes": ({"decision"}, {"decision"}),
    "reverses": ({"decision"}, {"decision"}),
    "amends": ({"decision"}, {"decision"}),
    "realizes": (None, {"risk"}),
    "mitigates": (None, {"risk"}),
    "learned_from": ({"lesson"}, None),
}
# kinds that form a decision's evolution chain
CHAIN_KINDS = {"supersedes", "reverses", "amends"}

DEFAULT_STATUS = {"decision": "active", "risk": "open"}
DECLARABLE_STATUSES = {
    "decision": ["proposed", "active", "deprecated", "rejected", "reversed", "superseded"],
    "risk": ["open", "mitigated", "materialized", "closed"],
}
# (incoming edge kind, resulting status), strongest first
_EDGE_STATUS = {
    "decision": [("reverses", "reversed"), ("supersedes", "superseded")],
    "risk": [("realizes", "materialized"), ("mitigates", "mitigated")],
}

# cosine similarity a candidate needs before it is suggested as a link
SUGGEST_MIN_SCORE = 0.5


class LineageError(ValueError):
    """A link that breaks the rules above; the router turns it into a 4xx."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def derive_status(item_type: str, declared: Optional[str], incoming_kinds: Iterable[str]) -> Optional[str]:
    default = DEFAULT_STATUS.get(item_type)
    if default is None:
        return None
    kinds = set(incoming_kinds)
    for kind, status in _EDGE_STATUS[item_type]:
        if kind in kinds:
            return status
    return declared or default


async def refresh_statuses(session: ArcadeSession, user_id: str) -> List[Row]:
    """
    Recompute the status of every decision and risk the user owns; store and
    log the ones that changed. Two reads, writes only for changes. No commit.
    """
    incoming: Dict[str, Set[str]] = defaultdict(set)
    for edge in await LineageRepository(session).list(user_id):
        incoming[edge["to"]].add(edge["kind"])
    rows = await session.query(
        "SELECT id, type, status, declared_status FROM KnowledgeItem "
        "WHERE user_id = :u AND type IN :types", {"u": user_id, "types": list(DEFAULT_STATUS)},
    )
    items = KnowledgeItemRepository(session)
    events = ItemEventRepository(session)
    changes: List[Row] = []
    for row in rows:
        new = derive_status(row["type"], row.get("declared_status"), incoming.get(row["id"], ()))
        old = row.get("status")
        if new == old:
            continue
        await items.update(user_id, row["id"], {"status": new})
        if old:  # the first status an item gets isn't a change worth logging
            await events.add(user_id, row["id"], "status_changed", {"from": old, "to": new})
        changes.append({"id": row["id"], "from": old, "to": new})
    return changes


def _reaches(edges: List[Row], start: str, goal: str) -> bool:
    """Whether `goal` can be reached from `start` following chain edges newer -> older."""
    out: Dict[str, List[str]] = defaultdict(list)
    for e in edges:
        if e["kind"] in CHAIN_KINDS:
            out[e["from"]].append(e["to"])
    seen, queue = {start}, deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            return True
        for nxt in out[node]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return False


def validate_link(source: Row, target: Row, kind: str, edges: List[Row]) -> None:
    if kind not in LINK_KINDS:
        raise LineageError(f"Unknown link kind '{kind}'. Use one of: {', '.join(LINK_KINDS)}")
    if source["id"] == target["id"]:
        raise LineageError("An item can't be linked to itself")
    src_types, dst_types = LINK_KINDS[kind]
    if src_types and source["type"] not in src_types:
        raise LineageError(f"'{kind}' links start from a {' or '.join(sorted(src_types))}, not a {source['type']}")
    if dst_types and target["type"] not in dst_types:
        raise LineageError(f"'{kind}' links point to a {' or '.join(sorted(dst_types))}, not a {target['type']}")
    pair = {source["id"], target["id"]}
    for e in edges:
        if {e["from"], e["to"]} == pair:
            raise LineageError(f"These items are already linked ({e['kind']})", status_code=409)
    if kind in CHAIN_KINDS and _reaches(edges, target["id"], source["id"]):
        raise LineageError("That link would make the decision history circular", status_code=409)


async def add_link(
    session: ArcadeSession, user_id: str, source_id: str, target_id: str, kind: str,
    note: str = "", origin: str = "manual", actor: Optional[str] = None,
) -> List[Row]:
    """Validate, create the edge, log it on both items, refresh statuses. Commits. Returns status changes."""
    items = KnowledgeItemRepository(session)
    source = await items.get_owned(user_id, source_id)
    target = await items.get_owned(user_id, target_id)
    if not source or not target:
        raise LineageError("Item not found", status_code=404)
    lineage = LineageRepository(session)
    validate_link(source, target, kind, await lineage.list(user_id))
    await lineage.add(user_id, source_id, target_id, kind, note, origin)
    events = ItemEventRepository(session)
    await events.add(user_id, source_id, "linked",
                     {"direction": "out", "kind": kind, "item_id": target_id, "title": target["title"], "note": note},
                     actor=actor)
    await events.add(user_id, target_id, "linked",
                     {"direction": "in", "kind": kind, "item_id": source_id, "title": source["title"], "note": note},
                     actor=actor)
    changes = await refresh_statuses(session, user_id)
    await session.commit()
    return changes


async def remove_link(session: ArcadeSession, user_id: str, source_id: str, target_id: str,
                      actor: Optional[str] = None) -> List[Row]:
    """Remove the edge source -> target (either direction is accepted). Commits."""
    lineage = LineageRepository(session)
    edge = next((e for e in await lineage.touching(user_id, source_id)
                 if {e["from"], e["to"]} == {source_id, target_id}), None)
    if not edge:
        raise LineageError("Link not found", status_code=404)
    await lineage.remove(user_id, edge["from"], edge["to"])
    events = ItemEventRepository(session)
    await events.add(user_id, edge["from"], "unlinked",
                     {"direction": "out", "kind": edge["kind"], "item_id": edge["to"]}, actor=actor)
    await events.add(user_id, edge["to"], "unlinked",
                     {"direction": "in", "kind": edge["kind"], "item_id": edge["from"]}, actor=actor)
    changes = await refresh_statuses(session, user_id)
    await session.commit()
    return changes


async def declare_status(session: ArcadeSession, user_id: str, item: Row, status: Optional[str], note: str,
                         actor: Optional[str] = None) -> Row:
    """Set (or clear, with None) the user's declared status. Commits."""
    allowed = DECLARABLE_STATUSES.get(item["type"])
    if allowed is None:
        raise LineageError(f"Only decisions and risks have a status, not a {item['type']}")
    if status is not None and status not in allowed:
        raise LineageError(f"A {item['type']} status must be one of: {', '.join(allowed)}")
    await KnowledgeItemRepository(session).update(user_id, item["id"], {"declared_status": status})
    await ItemEventRepository(session).add(user_id, item["id"], "status_declared", {"status": status, "note": note},
                                           actor=actor)
    await refresh_statuses(session, user_id)
    await session.commit()
    return await KnowledgeItemRepository(session).get_owned(user_id, item["id"])


def _brief(item: Row) -> Row:
    return {k: item.get(k) for k in ("id", "title", "type", "date", "status", "artifact_id", "review_status")}


async def item_lineage(session: ArcadeSession, user_id: str, item: Row) -> Row:
    """Everything the detail page shows about an item's history."""
    all_edges = await LineageRepository(session).list(user_id)
    by_id = {i["id"]: i for i in await KnowledgeItemRepository(session).list(user_id)}

    links = {"incoming": [], "outgoing": []}
    for e in all_edges:
        if item["id"] not in (e["from"], e["to"]):
            continue
        outgoing = e["from"] == item["id"]
        other = by_id.get(e["to"] if outgoing else e["from"])
        if other:
            links["outgoing" if outgoing else "incoming"].append(
                {"kind": e["kind"], "note": e["note"], "origin": e["origin"], "created_at": e["created_at"],
                 "item": _brief(other)})

    # the decision's evolution chain: connected component over chain edges, oldest first
    neighbours: Dict[str, Set[str]] = defaultdict(set)
    for e in all_edges:
        if e["kind"] in CHAIN_KINDS:
            neighbours[e["from"]].add(e["to"])
            neighbours[e["to"]].add(e["from"])
    chain_ids, queue = {item["id"]}, deque([item["id"]])
    while queue:
        for nxt in neighbours[queue.popleft()]:
            if nxt not in chain_ids:
                chain_ids.add(nxt)
                queue.append(nxt)
    chain = sorted((_brief(by_id[i]) for i in chain_ids if i in by_id), key=lambda i: i.get("date") or "")
    chain_edges = [e for e in all_edges if e["kind"] in CHAIN_KINDS and e["from"] in chain_ids]

    return {
        "item": item_dict(item),
        "status": item.get("status"),
        "declared_status": item.get("declared_status"),
        "declarable_statuses": DECLARABLE_STATUSES.get(item["type"], []),
        "links": links,
        "chain": chain if len(chain) > 1 else [],
        "chain_edges": chain_edges,
        "events": await ItemEventRepository(session).list(user_id, item["id"]),
    }


def _suggested_link(item: Row, cand: Row) -> Optional[Row]:
    """Which kind + direction a similar candidate most plausibly has with `item`."""
    it, ct = item["type"], cand["type"]
    if it == "decision" and ct == "decision":
        # the later decision supersedes the earlier one
        newer, older = (cand, item) if (cand.get("date") or "") > (item.get("date") or "") else (item, cand)
        return {"kind": "supersedes", "from": newer["id"], "to": older["id"]}
    if it == "risk" or ct == "risk":
        risk, other = (item, cand) if it == "risk" else (cand, item)
        if other["type"] == "risk":
            return None
        kind = "realizes" if other["type"] == "lesson" else "mitigates"
        return {"kind": kind, "from": other["id"], "to": risk["id"]}
    if it == "lesson":
        return {"kind": "learned_from", "from": item["id"], "to": cand["id"]}
    if ct == "lesson":
        return {"kind": "learned_from", "from": cand["id"], "to": item["id"]}
    return None


async def suggest_links(session: ArcadeSession, user_id: str, item: Row, limit: int = 5) -> List[Row]:
    """
    Similar items from other artifacts that probably relate to this one over
    time (an older decision this one replaces, a lesson that shows a risk
    happened, ...). Already-linked items are left out.
    """
    if item["type"] not in ("decision", "risk", "lesson"):
        return []
    vector = await session.scalar(
        "SELECT embedding FROM KnowledgeItem WHERE id = :id AND user_id = :u", {"id": item["id"], "u": user_id}
    )
    if not vector:
        return []
    linked = {node for e in await LineageRepository(session).touching(user_id, item["id"])
              for node in (e["from"], e["to"])}
    wanted = {"decision": {"decision", "lesson"},
              "risk": {"lesson", "decision", "action-item", "how-to", "best-practice"},
              "lesson": {"risk", "decision"}}[item["type"]]
    items = KnowledgeItemRepository(session)
    out: List[Row] = []
    for hit in await GraphStore(session).vector_search(user_id, vector, top_k=30):
        if (hit["id"] == item["id"] or hit["id"] in linked or hit.get("kind") not in wanted
                or hit.get("artifact_id") == item.get("artifact_id") or hit["score"] < SUGGEST_MIN_SCORE):
            continue
        cand = await items.get_owned(user_id, hit["id"])
        link = _suggested_link(item, cand) if cand else None
        if link:
            out.append({**link, "score": round(hit["score"], 3), "item": _brief(cand)})
        if len(out) >= limit:
            break
    return out


async def register(session: ArcadeSession, user_id: str, types: List[str]) -> Row:
    """The decision / risk register: tracked items with their status and chain edges."""
    rows = [i for i in await KnowledgeItemRepository(session).list(user_id, exclude_rejected=True)
            if i["type"] in types]
    ids = {i["id"] for i in rows}
    edges = [e for e in await LineageRepository(session).list(user_id) if e["from"] in ids or e["to"] in ids]
    return {"items": [item_dict(i) for i in rows], "edges": edges}
