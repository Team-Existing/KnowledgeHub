"""
Decision Copilot (services/copilot.py): settings on a GitHub, Jira or Linear
connector, on-demand previews and comments, the run history, and the webhooks.

  GET/PUT /connectors/{id}/copilot             enable or disable; the webhook secret
  POST    /knowledge/copilot/preview           the comment a pull request or issue would get, without posting
  POST    /knowledge/copilot/post              post (or update) the comment now
  GET     /knowledge/copilot/runs              recent runs in the space
  POST    /copilot/{kind}/{id}/webhook         the platform's events (public, verified)

GitHub and Jira use a secret Knowledge Hubs creates (shown once, to paste
into the webhook's settings). Linear creates the signing secret itself, so it
is pasted here instead.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import coordination
from app.arcadedb import ArcadeSession
from app.db import get_session
from app.repositories import ConnectorRepository
from app.services import copilot
from app.spaces import Space, current_space

router = APIRouter(tags=["copilot"])

Row = Dict[str, Any]


class CopilotSettings(BaseModel):
    enabled: bool
    rotate_secret: bool = False                                      # GitHub, Jira: a new secret
    webhook_secret: Optional[str] = Field(default=None, min_length=8, max_length=256)   # Linear: its signing secret


class TargetRef(BaseModel):
    connector_id: str = Field(min_length=1)
    ref: str = Field(min_length=1, max_length=40)                    # 42, ARCH-12, ENG-42


def _webhook_url(request: Request, connector: Row) -> str:
    public = (os.getenv("PUBLIC_URL", "").strip() or str(request.base_url)).rstrip("/")
    return f"{public}/copilot/{connector['kind']}/{connector['id']}/webhook"


async def _connector(session: ArcadeSession, space: Space, connector_id: str) -> Row:
    connector = await ConnectorRepository(session).get_owned(space.id, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    if connector.get("kind") not in copilot.KINDS:
        raise HTTPException(status_code=400, detail="The Decision Copilot works with GitHub, Jira and Linear connectors")
    return connector


# ── settings ────────────────────────────────────────────────────────────────

@router.get("/connectors/{connector_id}/copilot")
async def get_settings(connector_id: str, request: Request, space: Space = Depends(current_space),
                       session: ArcadeSession = Depends(get_session)) -> Row:
    connector = await _connector(session, space, connector_id)
    return {**copilot.settings(connector), "webhook_url": _webhook_url(request, connector)}


@router.put("/connectors/{connector_id}/copilot")
async def put_settings(connector_id: str, body: CopilotSettings, request: Request,
                       space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> Row:
    """
    GitHub, Jira: turning it on the first time (or rotating) creates the secret, returned
    only in this response. Linear: send the signing secret Linear shows for the webhook.
    """
    connector = await _connector(session, space, connector_id)
    stored = dict(connector.get("copilot") or {})
    stored["enabled"] = body.enabled
    created = None
    if connector["kind"] == "linear":
        if body.webhook_secret:
            _, stored["webhook_secret"] = copilot.seal_secret(connector_id, body.webhook_secret.strip())
        elif body.enabled and not stored.get("webhook_secret"):
            raise HTTPException(status_code=422, detail="Paste the signing secret Linear shows for the webhook")
    elif body.enabled and (body.rotate_secret or not stored.get("webhook_secret")):
        created, stored["webhook_secret"] = copilot.seal_secret(connector_id)
    await ConnectorRepository(session).update(space.id, connector_id, {"copilot": stored})
    await session.commit()
    out = {**copilot.settings({"copilot": stored}), "webhook_url": _webhook_url(request, connector)}
    if created:
        out["webhook_secret"] = created
    return out


# ── on demand ───────────────────────────────────────────────────────────────

@router.post("/knowledge/copilot/preview")
async def preview(body: TargetRef, space: Space = Depends(current_space),
                  session: ArcadeSession = Depends(get_session)) -> Row:
    connector = await _connector(session, space, body.connector_id)
    try:
        result = await copilot.analyze(session, space.id, connector, body.ref.strip())
    except copilot.CopilotError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {k: v for k, v in result.items() if not k.startswith("_")}


@router.post("/knowledge/copilot/post")
async def post_now(body: TargetRef, space: Space = Depends(current_space),
                   session: ArcadeSession = Depends(get_session)) -> Row:
    """Post or update the comment, whether or not the analysis found it relevant: someone asked for it."""
    connector = await _connector(session, space, body.connector_id)
    result = await copilot.run(session, space.id, connector, body.ref.strip(), "manual", post=True)
    if result["status"] == "failed":
        raise HTTPException(status_code=502, detail=result["reason"])
    return result


@router.get("/knowledge/copilot/runs")
async def list_runs(space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> List[Row]:
    return await copilot.runs(session, space.id)


# ── webhooks ────────────────────────────────────────────────────────────────

@router.post("/copilot/{kind}/{connector_id}/webhook", status_code=202)
async def webhook(kind: str, connector_id: str, request: Request, background: BackgroundTasks,
                  session: ArcadeSession = Depends(get_session)) -> Response:
    """
    Verified with the connector's secret, or refused. Answers at once (GitHub gives up
    after 10 seconds) and analyses afterwards.
    """
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    connector = await session.query_one("SELECT FROM Connector WHERE id = :id", {"id": connector_id})
    # the same answer for an unknown connector, the wrong kind and a bad signature: nothing to probe
    if not connector or connector.get("kind") != kind or not copilot.verify(connector, body, headers):
        raise HTTPException(status_code=401, detail="Invalid signature")
    if not copilot.settings(connector)["enabled"]:
        return Response(status_code=204)
    try:
        event = json.loads(body) if body else {}
    except ValueError:
        raise HTTPException(status_code=400, detail="Not JSON")
    ref = copilot.target_of_event(kind, headers, event if isinstance(event, dict) else {})
    if not ref:
        return Response(status_code=204)               # pings and events the Copilot doesn't act on
    await coordination.dispatch_copilot(connector["user_id"], connector_id, str(ref), background)
    return Response(status_code=202)
