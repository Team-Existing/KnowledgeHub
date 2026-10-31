"""Institutional learning (services/learning.py): the knowledge map, what was abandoned, recurring mistakes, controversy."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from app.arcadedb import ArcadeSession
from app.db import get_session
from app.services import learning
from app.spaces import Space, current_space

router = APIRouter(prefix="/knowledge/learning", tags=["learning"])

Row = Dict[str, Any]


@router.get("/map")
async def knowledge_map(system: List[str] = Query(default=[]), space: Space = Depends(current_space),
                        session: ArcadeSession = Depends(get_session)) -> Row:
    """What to understand about these systems (all of them when none is given)."""
    return await learning.knowledge_map(session, space.id, system)


@router.get("/abandoned")
async def abandoned(space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> Row:
    return await learning.abandoned(session, space.id)


@router.get("/mistakes")
async def mistakes(space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> Row:
    return await learning.mistakes(session, space.id)


@router.get("/controversial")
async def controversial(space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> Row:
    return await learning.controversial(session, space.id)
