"""Read-only API keys for a space (app/api_keys.py): list, create (the key is shown once), revoke."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app import api_keys
from app.arcadedb import ArcadeSession
from app.db import get_session
from app.spaces import Space, current_space

router = APIRouter(prefix="/knowledge/api-keys", tags=["api-keys"])

Row = Dict[str, Any]


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)    # what uses it, e.g. "Backstage"


@router.get("")
async def list_keys(space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> List[Row]:
    return await api_keys.list_keys(session, space.id)


@router.post("", status_code=201)
async def create_key(body: KeyCreate, space: Space = Depends(current_space),
                     session: ArcadeSession = Depends(get_session)) -> Row:
    """The response holds the key; it's never shown again (only its hash is stored)."""
    return await api_keys.create(session, space.id, body.name.strip(), space.user.id, space.actor)


@router.delete("/{key_id}", status_code=204)
async def revoke_key(key_id: str, space: Space = Depends(current_space),
                     session: ArcadeSession = Depends(get_session)) -> Response:
    if not await api_keys.delete(session, space.id, key_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return Response(status_code=204)
