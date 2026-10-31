"""Knowledge health: what may be out of date, flagging and confirming items, review dates."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app.db import get_session
from app.repositories import KnowledgeItemRepository
from app.serializers import item_dict
from app.services import health, verification
from app.spaces import Space, current_space

router = APIRouter(prefix="/knowledge", tags=["health"])

Row = Dict[str, Any]


class FlagRequest(BaseModel):
    note: str = Field(min_length=3, max_length=1000)   # why it may be outdated: the next reader needs it


class VerifyRequest(BaseModel):
    note: str = Field(default="", max_length=1000)
    review_due: Optional[date] = None   # the next review date; None = no date


class ReviewDateRequest(BaseModel):
    review_due: Optional[date] = None   # None clears it


async def _owned_item(session: ArcadeSession, space: Space, item_id: str) -> Row:
    item = await KnowledgeItemRepository(session).get_owned(space.id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.get("/health")
async def attention(
    response: Response,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Current items that may be out of date or are due for review, most urgent first."""
    result = await health.attention(session, space.id, limit, offset)
    response.headers["X-Total-Count"] = str(result["total"])
    return {"items": [item_dict(i) for i in result["items"]], "total": result["total"], "counts": result["counts"]}


@router.post("/items/{item_id}/health/check")
async def check_consistency(
    item_id: str, space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Compare the item with similar current knowledge now: duplicates and contradictions (services/verification.py)."""
    await _owned_item(session, space, item_id)
    found = await verification.check(session, space.id, item_id, space.actor)
    item = await KnowledgeItemRepository(session).get_owned(space.id, item_id)
    return {**found, "item": item_dict(item)}


@router.post("/items/{item_id}/health/flag")
async def flag(
    item_id: str, body: FlagRequest,
    space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Mark the item as possibly outdated, with the reason."""
    item = await _owned_item(session, space, item_id)
    return item_dict(await health.flag(session, space.id, item, body.note.strip(), space.actor))


@router.post("/items/{item_id}/health/verify")
async def verify(
    item_id: str, body: VerifyRequest,
    space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Confirm the item still holds: clears its signals and sets (or clears) the next review date."""
    item = await _owned_item(session, space, item_id)
    due = body.review_due.isoformat() if body.review_due else None
    return item_dict(await health.verify(session, space.id, item, body.note.strip(), space.actor, due))


@router.put("/items/{item_id}/review-date")
async def set_review_date(
    item_id: str, body: ReviewDateRequest,
    space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    item = await _owned_item(session, space, item_id)
    due = body.review_due.isoformat() if body.review_due else None
    return item_dict(await health.set_review_date(session, space.id, item, due, space.actor))
