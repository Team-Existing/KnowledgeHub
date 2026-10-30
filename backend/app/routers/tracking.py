"""Commitments and outcomes (services/tracking.py): lists, and setting owners, due dates, expectations and verdicts."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app.db import get_session
from app.repositories import KnowledgeItemRepository
from app.serializers import item_dict
from app.services import tracking
from app.spaces import Space, current_space

router = APIRouter(prefix="/knowledge", tags=["tracking"])

Row = Dict[str, Any]


class CommitmentRequest(BaseModel):
    """Only the fields sent change; null clears one."""
    owner: Optional[str] = Field(default=None, max_length=120)
    due_date: Optional[date] = None


class ExpectationRequest(BaseModel):
    expected: Optional[str] = Field(default=None, max_length=1000)   # what the decision should achieve
    check_by: Optional[date] = None                                  # when to see whether it did


class OutcomeRequest(BaseModel):
    verdict: Optional[Literal["achieved", "partly", "not_achieved"]] = None   # null withdraws the verdict
    note: str = Field(default="", max_length=2000)                            # the evidence, in words


async def _owned_item(session: ArcadeSession, space: Space, item_id: str) -> Row:
    item = await KnowledgeItemRepository(session).get_owned(space.id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.get("/commitments")
async def list_commitments(
    response: Response,
    show: Literal["all", "open", "overdue", "done"] = "open",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    """Commitments (action items), overdue first, then by due date; with the decision each carries out."""
    result = await tracking.commitments(session, space.id, show, limit, offset)
    response.headers["X-Total-Count"] = str(result["total"])
    return result


@router.put("/items/{item_id}/commitment")
async def update_commitment(
    item_id: str, body: CommitmentRequest,
    space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session),
) -> Row:
    item = await _owned_item(session, space, item_id)
    fields: Row = {}
    if "owner" in body.model_fields_set:
        fields["owner"] = (body.owner or "").strip() or None
    if "due_date" in body.model_fields_set:
        fields["due_date"] = body.due_date.isoformat() if body.due_date else None
    try:
        return item_dict(await tracking.update_commitment(session, space.id, item, fields, space.actor))
    except tracking.TrackingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/outcomes")
async def list_outcomes(space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> Row:
    """Decisions with an expected outcome: waiting for a verdict (due first), then decided, with their evidence."""
    return await tracking.outcomes(session, space.id)


@router.put("/items/{item_id}/outcome/expectation")
async def set_expectation(
    item_id: str, body: ExpectationRequest,
    space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session),
) -> Row:
    item = await _owned_item(session, space, item_id)
    try:
        updated = await tracking.set_expectation(session, space.id, item, (body.expected or "").strip() or None,
                                                 body.check_by.isoformat() if body.check_by else None, space.actor)
    except tracking.TrackingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return item_dict(updated)


@router.put("/items/{item_id}/outcome")
async def record_outcome(
    item_id: str, body: OutcomeRequest,
    space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session),
) -> Row:
    item = await _owned_item(session, space, item_id)
    try:
        updated = await tracking.record_outcome(session, space.id, item, body.verdict, body.note.strip(), space.actor)
    except tracking.TrackingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return item_dict(updated)
