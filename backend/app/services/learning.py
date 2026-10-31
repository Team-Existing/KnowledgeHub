"""
Institutional learning: what a newcomer (or anyone about to change something)
should understand, built from the record rather than read from hundreds of documents.

  map           per system: the current decisions and why, how they came to be (the
                chain of what replaced what, with outcomes), open risks, lessons, the
                people involved, dependencies both ways, and commitments in flight.
                "What should I understand before modifying X?" / "Why does X look this way?"
  abandoned     approaches tried and given up: superseded, reversed and invalidated
                decisions with what replaced them and why, rejected alternatives,
                outcomes that weren't achieved, dropped commitments
  mistakes      recurring production mistakes: lessons and risks that happened, grouped
                by similarity, the biggest groups first, with the systems they touch
  controversial decisions with signs of disagreement: changed back and forth, amended
                often, contradicted, flagged, or an outcome that wasn't (fully) achieved

No LLM calls: all of it comes from statuses, links, outcomes, signals and
embeddings already in the graph, so it's fast and every entry can be checked.
Plain-English questions go to GraphRAG.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from app.arcadedb import ArcadeSession
from app.repositories import KnowledgeItemRepository, LineageRepository
from app.repositories.systems import SystemRepository
from app.services import tracking
from app.services.health import health_of
from app.services.impact import item_text, mentions

Row = Dict[str, Any]

CURRENT = {"active", "proposed"}
ABANDONED = {"superseded", "reversed", "invalidated", "deprecated"}
MISTAKE_SIMILARITY = 0.6     # lessons / risks at least this similar count as the same mistake
_LISTED = 8


def _brief(item: Row) -> Row:
    details = item.get("details") or {}
    return {"id": item["id"], "title": item.get("title"), "type": item.get("type"), "status": item.get("status"),
            "date": item.get("date"), "why": details.get("why"), "who": details.get("who") or item.get("author")}


async def _load(session: ArcadeSession, user_id: str) -> Row:
    items = {i["id"]: i for i in await KnowledgeItemRepository(session).list(user_id, exclude_rejected=True)}
    edges = await LineageRepository(session).list(user_id)
    systems = {s["id"]: s for s in await SystemRepository(session).list(user_id)}
    deps = await SystemRepository(session).dependencies(user_id)
    affects = defaultdict(set)
    for a in await SystemRepository(session).affects(user_id):
        affects[a["system_id"]].add(a["item_id"])
    return {"items": items, "edges": edges, "systems": systems, "deps": deps, "affects": affects}


def _about(data: Row, system: Row) -> Set[str]:
    """Items linked to the system or naming it."""
    linked = set(data["affects"][system["id"]])
    return linked | {i for i, item in data["items"].items() if i not in linked and mentions(item_text(item), system)}


def _replaced_by(edges: List[Row]) -> Dict[str, Row]:
    return {e["to"]: e for e in edges if e["kind"] in ("supersedes", "reverses")}


# ── the knowledge map ───────────────────────────────────────────────────────

async def knowledge_map(session: ArcadeSession, user_id: str, system_ids: List[str]) -> Row:
    data = await _load(session, user_id)
    items, edges, systems = data["items"], data["edges"], data["systems"]
    replaced = _replaced_by(edges)
    implementing = defaultdict(list)
    for e in edges:
        if e["kind"] == "implements":
            implementing[e["to"]].append(e["from"])
    out = []
    for sid in [s for s in system_ids if s in systems] or list(systems):
        system = systems[sid]
        about = [items[i] for i in _about(data, system) if i in items]
        decisions = sorted((i for i in about if i["type"] == "decision"), key=lambda i: i.get("date") or "")
        current = [d for d in decisions if (d.get("status") or "active") in CURRENT]
        history = []
        for d in decisions:
            entry = {**_brief(d), "outcome": tracking.outcome_view(d) if d.get("outcome") else None}
            newer = replaced.get(d["id"])
            if newer and newer["from"] in items:
                entry["replaced_by"] = {"id": newer["from"], "title": items[newer["from"]].get("title"), "kind": newer["kind"]}
            history.append(entry)
        risks = [i for i in about if i["type"] == "risk" and (i.get("status") or "open") in ("open", "materialized")]
        lessons = [i for i in about if i["type"] == "lesson"]
        commitments = [i for i in about if i["type"] == "action-item" and (i.get("status") or "open") not in tracking.FINISHED]
        for d in current:
            commitments += [items[c] for c in implementing[d["id"]]
                            if c in items and (items[c].get("status") or "open") not in tracking.FINISHED and items[c] not in commitments]
        people = defaultdict(int)
        for i in about:
            who = (i.get("details") or {}).get("who") or i.get("author")
            if who and who not in ("unknown", "several"):
                people[who] += 1
        out.append({
            "system": {k: system.get(k) for k in ("id", "name", "kind", "owner", "description")},
            "depends_on": [systems[d["to"]]["name"] for d in data["deps"] if d["from"] == sid and d["to"] in systems],
            "dependents": [systems[d["from"]]["name"] for d in data["deps"] if d["to"] == sid and d["from"] in systems],
            "current": [{**_brief(d), "health": health_of(d)["state"],
                         "alternatives": (d.get("details") or {}).get("alternatives")} for d in current],
            "history": history,
            "risks": [_brief(r) | {"severity": (r.get("details") or {}).get("severity")} for r in risks],
            "lessons": [_brief(l) for l in lessons],
            "commitments": [_brief(c) | {"commitment": tracking.commitment_view(c)} for c in commitments],
            "people": [{"name": n, "items": c} for n, c in sorted(people.items(), key=lambda kv: -kv[1])][:6],
        })
    return {"systems": out, "all_systems": [{"id": s["id"], "name": s["name"]} for s in systems.values()]}


# ── tried and abandoned ─────────────────────────────────────────────────────

async def abandoned(session: ArcadeSession, user_id: str) -> Row:
    data = await _load(session, user_id)
    items, edges = data["items"], data["edges"]
    replaced = _replaced_by(edges)
    approaches = []
    for d in items.values():
        if d["type"] != "decision":
            continue
        status = d.get("status") or "active"
        entry: Optional[Row] = None
        if status in ABANDONED:
            newer = replaced.get(d["id"])
            entry = {**_brief(d), "how": status,
                     "replaced_by": ({"id": newer["from"], "title": items[newer["from"]].get("title"),
                                      "why": (items[newer["from"]].get("details") or {}).get("why")}
                                     if newer and newer["from"] in items else None)}
        elif d.get("outcome") == "not_achieved":
            entry = {**_brief(d), "how": "outcome not achieved", "replaced_by": None, "note": d.get("outcome_note")}
        if entry:
            approaches.append(entry)
    rejected = [{"option": alt, "instead": {"id": d["id"], "title": d.get("title")}, "why": (d.get("details") or {}).get("why")}
                for d in items.values() if d["type"] == "decision"
                for alt in ((d.get("details") or {}).get("alternatives") or [])]
    dropped = [_brief(c) for c in items.values() if c["type"] == "action-item" and c.get("status") == "dropped"]
    approaches.sort(key=lambda e: e.get("date") or "", reverse=True)
    return {"decisions": approaches, "rejected_alternatives": rejected, "dropped_commitments": dropped}


# ── recurring mistakes ──────────────────────────────────────────────────────

def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def mistakes(session: ArcadeSession, user_id: str) -> Row:
    """Lessons and risks that happened, grouped greedily by similarity; groups of the same mistake first."""
    data = await _load(session, user_id)
    items = data["items"]
    happened = {e["to"] for e in data["edges"] if e["kind"] == "realizes"}
    pool = [i for i in items.values()
            if i["type"] == "lesson" or (i["type"] == "risk" and (i.get("status") == "materialized" or i["id"] in happened))]
    vectors = {r["id"]: r["embedding"] for r in await session.query(
        "SELECT id, embedding FROM KnowledgeItem WHERE user_id = :u AND id IN :ids AND embedding IS NOT NULL",
        {"u": user_id, "ids": [i["id"] for i in pool] or [""]})}
    groups: List[Row] = []
    for item in sorted(pool, key=lambda i: i.get("date") or ""):
        vec = vectors.get(item["id"])
        home = None
        if vec:
            home = max(groups, key=lambda g: _cosine(vec, g["vector"]), default=None)
            if home and _cosine(vec, home["vector"]) < MISTAKE_SIMILARITY:
                home = None
        if home is None:
            home = {"vector": vec or [], "items": []}
            groups.append(home)
        home["items"].append(item)
    out = []
    for g in groups:
        members = g["items"]
        touched = sorted({s["name"] for s in data["systems"].values()
                          if any(m["id"] in data["affects"][s["id"]] or mentions(item_text(m), s) for m in members)})
        out.append({"title": members[0].get("title"), "count": len(members), "systems": touched,
                    "items": [_brief(m) for m in members][:_LISTED],
                    "latest": max((m.get("date") or "") for m in members)})
    out.sort(key=lambda g: g["latest"], reverse=True)       # recent first …
    out.sort(key=lambda g: -g["count"])                      # … within the most repeated first (a stable sort)
    return {"groups": out}


# ── controversial decisions ─────────────────────────────────────────────────

async def controversial(session: ArcadeSession, user_id: str) -> Row:
    """Decisions with signs of disagreement, most first. Each reason counts once."""
    data = await _load(session, user_id)
    items, edges = data["items"], data["edges"]
    links = defaultdict(list)
    for e in edges:
        if e["kind"] in ("supersedes", "reverses", "amends"):
            links[e["to"]].append(e)
            links[e["from"]].append(e)
    ranked = []
    for d in items.values():
        if d["type"] != "decision":
            continue
        reasons = []
        kinds = [e["kind"] for e in links[d["id"]]]
        if "reverses" in kinds:
            reasons.append("reversed, or reverses an earlier decision")
        if kinds.count("amends") >= 2:
            reasons.append(f"amended {kinds.count('amends')} times")
        chain = _chain_length(d["id"], edges)
        if chain >= 3:
            reasons.append(f"part of a {chain}-step decision history")
        signals = [s.get("kind") for s in d.get("health_signals") or []]
        if "contradicted" in signals:
            reasons.append("contradicted by newer knowledge")
        if "flagged" in signals:
            reasons.append("flagged as possibly outdated")
        if d.get("outcome") in ("not_achieved", "partly"):
            reasons.append("its outcome was " + ("not achieved" if d["outcome"] == "not_achieved" else "only partly achieved"))
        if reasons:
            ranked.append({**_brief(d), "reasons": reasons, "score": len(reasons)})
    ranked.sort(key=lambda r: (-r["score"], r.get("date") or ""))
    return {"decisions": ranked}


def _chain_length(item_id: str, edges: List[Row]) -> int:
    """Decisions connected to this one by supersedes / reverses / amends, itself included."""
    neighbours = defaultdict(set)
    for e in edges:
        if e["kind"] in ("supersedes", "reverses", "amends"):
            neighbours[e["from"]].add(e["to"])
            neighbours[e["to"]].add(e["from"])
    seen, stack = {item_id}, [item_id]
    while stack:
        for n in neighbours[stack.pop()]:
            if n not in seen:
                seen.add(n)
                stack.append(n)
    return len(seen)
