"""Decision tracking over time: item lineage, statuses, history and the decision / risk register."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app.spaces import Space, current_space
from app.db import get_session
from app.repositories import KnowledgeItemRepository
from app.serializers import item_dict
from app.services import lineage
from app.services.lineage import LineageError

router = APIRouter(prefix="/knowledge", tags=["lineage"])

Row = Dict[str, Any]


class LinkRequest(BaseModel):
    target_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    note: str = Field(default="", max_length=1000)
    # "out": this item -kind-> target (default); "in": target -kind-> this item
    direction: Literal["out", "in"] = "out"


class StatusRequest(BaseModel):
    status: Optional[str] = None     # None clears the declared status
    note: str = Field(default="", max_length=1000)


async def _owned_item(session: ArcadeSession, space: Space, item_id: str) -> Row:
    item = await KnowledgeItemRepository(session).get_owned(space.id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


def _http(exc: LineageError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/lineage/kinds")
async def link_kinds(space: Space = Depends(current_space)) -> Dict[str, Any]:
    return {
        "kinds": [
            {"kind": kind, "from_types": sorted(src) if src else None, "to_types": sorted(dst) if dst else None}
            for kind, (src, dst) in lineage.LINK_KINDS.items()
        ],
        "statuses": lineage.DECLARABLE_STATUSES,
    }


@router.get("/register")
async def register(
    type: List[str] = Query(default=["decision"]),
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Decisions (and/or risks) with their status and lineage edges — the data behind the decision log."""
    return await lineage.register(session, space.id, type)


@router.get("/items/{item_id}/lineage")
async def get_lineage(
    item_id: str,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    item = await _owned_item(session, space, item_id)
    return await lineage.item_lineage(session, space.id, item)


@router.post("/items/{item_id}/lineage", status_code=201)
async def add_link(
    item_id: str,
    body: LinkRequest,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    await _owned_item(session, space, item_id)
    source, target = (item_id, body.target_id) if body.direction == "out" else (body.target_id, item_id)
    try:
        changes = await lineage.add_link(session, space.id, source, target, body.kind, body.note, actor=space.actor)
    except LineageError as exc:
        raise _http(exc)
    return {"from": source, "to": target, "kind": body.kind, "status_changes": changes}


@router.delete("/items/{item_id}/lineage/{other_id}")
async def remove_link(
    item_id: str,
    other_id: str,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    await _owned_item(session, space, item_id)
    try:
        changes = await lineage.remove_link(session, space.id, item_id, other_id, actor=space.actor)
    except LineageError as exc:
        raise _http(exc)
    return {"removed": True, "status_changes": changes}


@router.get("/items/{item_id}/lineage/suggestions")
async def link_suggestions(
    item_id: str,
    limit: int = Query(default=5, ge=1, le=20),
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> List[Dict[str, Any]]:
    item = await _owned_item(session, space, item_id)
    return await lineage.suggest_links(session, space.id, item, limit)


@router.put("/items/{item_id}/status")
async def declare_status(
    item_id: str,
    body: StatusRequest,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    Declare a decision's or risk's status by hand (e.g. a decision was dropped
    without a replacement, or a risk is closed). Lineage edges still win: an
    item another decision supersedes stays "superseded".
    """
    item = await _owned_item(session, space, item_id)
    try:
        return item_dict(await lineage.declare_status(session, space.id, item, body.status, body.note,
                                                      actor=space.actor))
    except LineageError as exc:
        raise _http(exc)
