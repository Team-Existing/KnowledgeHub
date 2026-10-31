"""
Systems (services, databases, teams …), their dependencies, which knowledge
items affect them, and decision impact analysis (services/impact.py).
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from app.arcadedb import ArcadeSession
from app.db import get_session
from app.repositories import KnowledgeItemRepository
from app.repositories.systems import SYSTEM_KINDS, SystemRepository
from app.services import impact
from app.spaces import Space, current_space

router = APIRouter(prefix="/knowledge", tags=["systems"])

Row = Dict[str, Any]
Kind = Literal["service", "database", "library", "infrastructure", "team", "product", "other"]


class SystemFields(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    kind: Optional[Kind] = None
    description: Optional[str] = Field(default=None, max_length=2000)
    aliases: Optional[List[str]] = Field(default=None, max_length=20)   # other names it goes by, for matching
    owner: Optional[str] = Field(default=None, max_length=120)

    @field_validator("aliases")
    @classmethod
    def _clean(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        return sorted({a.strip() for a in value if a.strip()} - {""}, key=str.lower)[:20]


class SystemCreate(SystemFields):
    name: str = Field(min_length=1, max_length=80)
    kind: Kind = "service"


class DependencyRequest(BaseModel):
    depends_on: str = Field(min_length=1)
    note: str = Field(default="", max_length=500)


class LinkRequest(BaseModel):
    system_id: str = Field(min_length=1)


class ImpactRequest(BaseModel):
    proposal: str = Field(default="", max_length=4000)
    system_ids: List[str] = Field(default_factory=list, max_length=50)
    decision_id: Optional[str] = None      # reconsider this decision (its systems are included)


async def _system(repo: SystemRepository, space: Space, system_id: str) -> Row:
    system = await repo.get(space.id, system_id)
    if not system:
        raise HTTPException(status_code=404, detail="System not found")
    return system


async def _unique_name(repo: SystemRepository, space: Space, name: str, except_id: Optional[str] = None) -> None:
    clash = await repo.by_name(space.id, name)
    if clash and clash["id"] != except_id:
        raise HTTPException(status_code=409, detail=f"There's already a system called {clash['name']}")


# ── systems ─────────────────────────────────────────────────────────────────

@router.get("/systems")
async def list_systems(space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> Row:
    """Systems with their dependencies (both ways) and how many knowledge items affect each."""
    repo = SystemRepository(session)
    deps = await repo.dependencies(space.id)
    counts: Dict[str, int] = {}
    for a in await repo.affects(space.id):
        counts[a["system_id"]] = counts.get(a["system_id"], 0) + 1
    systems = [{**s, "aliases": s.get("aliases") or [],
                "depends_on": [d["to"] for d in deps if d["from"] == s["id"]],
                "dependents": [d["from"] for d in deps if d["to"] == s["id"]],
                "item_count": counts.get(s["id"], 0)} for s in await repo.list(space.id)]
    return {"systems": systems, "kinds": list(SYSTEM_KINDS)}


@router.post("/systems", status_code=201)
async def create_system(body: SystemCreate, space: Space = Depends(current_space),
                        session: ArcadeSession = Depends(get_session)) -> Row:
    repo = SystemRepository(session)
    await _unique_name(repo, space, body.name.strip())
    system = await repo.create(space.id, {"name": body.name.strip(), "kind": body.kind,
                                          "description": (body.description or "").strip() or None,
                                          "aliases": body.aliases or [], "owner": (body.owner or "").strip() or None},
                               space.actor)
    await session.commit()
    return {k: v for k, v in system.items() if k != "user_id"}


@router.patch("/systems/{system_id}")
async def update_system(system_id: str, body: SystemFields, space: Space = Depends(current_space),
                        session: ArcadeSession = Depends(get_session)) -> Row:
    repo = SystemRepository(session)
    await _system(repo, space, system_id)
    fields: Row = {}
    for key in body.model_fields_set:
        value = getattr(body, key)
        fields[key] = value.strip() if isinstance(value, str) else value
    if fields.get("name"):
        await _unique_name(repo, space, fields["name"], except_id=system_id)
    elif "name" in fields:
        raise HTTPException(status_code=422, detail="A system needs a name")
    if fields:
        await repo.update(space.id, system_id, fields)
        await session.commit()
    return await repo.get(space.id, system_id)


@router.delete("/systems/{system_id}", status_code=204)
async def delete_system(system_id: str, space: Space = Depends(current_space),
                        session: ArcadeSession = Depends(get_session)) -> Response:
    """Its dependencies and item links go with it; the items stay."""
    repo = SystemRepository(session)
    await _system(repo, space, system_id)
    await repo.delete(space.id, system_id)
    await session.commit()
    return Response(status_code=204)


@router.post("/systems/{system_id}/dependencies", status_code=201)
async def add_dependency(system_id: str, body: DependencyRequest, space: Space = Depends(current_space),
                         session: ArcadeSession = Depends(get_session)) -> Row:
    repo = SystemRepository(session)
    await _system(repo, space, system_id)
    await _system(repo, space, body.depends_on)
    if body.depends_on == system_id:
        raise HTTPException(status_code=400, detail="A system can't depend on itself")
    await repo.add_dependency(space.id, system_id, body.depends_on, body.note.strip())
    await session.commit()
    return {"from": system_id, "to": body.depends_on, "note": body.note.strip()}


@router.delete("/systems/{system_id}/dependencies/{other_id}", status_code=204)
async def remove_dependency(system_id: str, other_id: str, space: Space = Depends(current_space),
                            session: ArcadeSession = Depends(get_session)) -> Response:
    await SystemRepository(session).remove_dependency(space.id, system_id, other_id)
    await session.commit()
    return Response(status_code=204)


# ── which systems an item affects ───────────────────────────────────────────

@router.get("/items/{item_id}/systems")
async def item_systems(item_id: str, space: Space = Depends(current_space),
                       session: ArcadeSession = Depends(get_session)) -> Row:
    """Linked systems, and unlinked ones the item names (suggested links)."""
    item = await KnowledgeItemRepository(session).get_owned(space.id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    repo = SystemRepository(session)
    systems = {s["id"]: s for s in await repo.list(space.id)}
    linked_ids = {a["system_id"] for a in await repo.affects(space.id) if a["item_id"] == item_id}
    text = impact.item_text(item)

    def brief(s: Row) -> Row:
        return {k: s.get(k) for k in ("id", "name", "kind")}
    return {"linked": [brief(systems[i]) for i in sorted(linked_ids, key=lambda i: systems[i]["name"]) if i in systems],
            "suggested": [brief(s) for s in systems.values() if s["id"] not in linked_ids and impact.mentions(text, s)]}


@router.post("/items/{item_id}/systems", status_code=201)
async def link_item(item_id: str, body: LinkRequest, space: Space = Depends(current_space),
                    session: ArcadeSession = Depends(get_session)) -> Row:
    if not await KnowledgeItemRepository(session).get_owned(space.id, item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    repo = SystemRepository(session)
    await _system(repo, space, body.system_id)
    await repo.link_item(space.id, item_id, body.system_id)
    await session.commit()
    return {"item_id": item_id, "system_id": body.system_id}


@router.delete("/items/{item_id}/systems/{system_id}", status_code=204)
async def unlink_item(item_id: str, system_id: str, space: Space = Depends(current_space),
                      session: ArcadeSession = Depends(get_session)) -> Response:
    await SystemRepository(session).unlink_item(space.id, item_id, system_id)
    await session.commit()
    return Response(status_code=204)


# ── impact analysis ─────────────────────────────────────────────────────────

@router.post("/impact")
async def analyze_impact(body: ImpactRequest, space: Space = Depends(current_space),
                         session: ArcadeSession = Depends(get_session)) -> Row:
    """What a proposed change touches, according to the record. Changes nothing."""
    if not body.proposal.strip() and not body.decision_id and not body.system_ids:
        raise HTTPException(status_code=422, detail="Describe the change, or pick a decision or systems")
    try:
        return await impact.analyze(session, space.id, body.proposal, body.system_ids, body.decision_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
