"""The weekly digest (services/digest.py): which Slack channels get it, a preview, and sending one now."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app.db import get_session
from app.services import digest
from app.spaces import Space, current_space

router = APIRouter(prefix="/knowledge/digests", tags=["digests"])

Row = Dict[str, Any]


class DigestCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)                  # e.g. "#platform-team"
    webhook_url: str = Field(min_length=20, max_length=300)         # a Slack incoming webhook
    weekday: int = Field(default=0, ge=0, le=6)                     # 0 = Monday


@router.get("")
async def list_digests(space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> Row:
    return {"digests": await digest.list_digests(session, space.id), "hour_utc": digest.digest_hour(),
            "weekdays": digest.WEEKDAYS}


@router.get("/preview")
async def preview(space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> Row:
    """What a digest of this space would say now."""
    return await digest.compose(session, space.id)


@router.post("", status_code=201)
async def add(body: DigestCreate, space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> Row:
    url = body.webhook_url.strip()
    if not digest.webhook_ok(url):
        raise HTTPException(status_code=422, detail="Use a Slack incoming webhook URL: https://hooks.slack.com/services/…")
    return await digest.create(session, space.id, body.name.strip(), url, body.weekday, space.actor)


@router.post("/{digest_id}/send")
async def send_now(digest_id: str, space: Space = Depends(current_space),
                   session: ArcadeSession = Depends(get_session)) -> Row:
    if not await session.query_one("SELECT id FROM Digest WHERE id = :id AND user_id = :u", {"id": digest_id, "u": space.id}):
        raise HTTPException(status_code=404, detail="Digest not found")
    result = await digest.send(session, digest_id, force=True)
    if result["status"].startswith("failed"):
        raise HTTPException(status_code=502, detail=result["status"])
    return result


@router.delete("/{digest_id}", status_code=204)
async def remove(digest_id: str, space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> Response:
    if not await digest.delete(session, space.id, digest_id):
        raise HTTPException(status_code=404, detail="Digest not found")
    return Response(status_code=204)
