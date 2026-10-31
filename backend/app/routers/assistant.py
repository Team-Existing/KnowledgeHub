"""The assistant (services/orchestrator.py): one question, the agents that answer it, and a check of the answer."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app.db import get_session
from app.services import orchestrator
from app.spaces import Space, current_space

router = APIRouter(prefix="/knowledge", tags=["assistant"])


class AssistantRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


@router.post("/assistant")
async def ask(body: AssistantRequest, space: Space = Depends(current_space),
              session: ArcadeSession = Depends(get_session)) -> Dict[str, Any]:
    """The plan, then each step's result. Reads only."""
    return await orchestrator.run(session, space.id, body.question.strip())
