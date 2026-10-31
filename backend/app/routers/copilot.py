"""
Decision Copilot (services/copilot.py): settings on a GitHub connector,
on-demand previews and comments, the run history, and GitHub's webhook.

  GET/PUT /connectors/{id}/copilot             enable or disable; the webhook secret is shown once
  POST    /knowledge/copilot/preview           the comment a PR would get, without posting
  POST    /knowledge/copilot/post              post (or update) the comment now
  GET     /knowledge/copilot/runs              recent runs in the space
  POST    /copilot/github/{id}/webhook         GitHub's pull_request events (public, signed)
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app import coordination
from app.db import get_session
from app.repositories import ConnectorRepository
from app.services import copilot
from app.spaces import Space, current_space

router = APIRouter(tags=["copilot"])

Row = Dict[str, Any]


class CopilotSettings(BaseModel):
    enabled: bool
    rotate_secret: bool = False     # a new webhook secret (the old one stops working)


class PullRequestRef(BaseModel):
    connector_id: str = Field(min_length=1)
    pr_number: int = Field(ge=1)


def _webhook_url(request: Request, connector_id: str) -> str:
    public = (os.getenv("PUBLIC_URL", "").strip() or str(request.base_url)).rstrip("/")
    return f"{public}/copilot/github/{connector_id}/webhook"


async def _github_connector(session: ArcadeSession, space: Space, connector_id: str) -> Row:
    connector = await ConnectorRepository(session).get_owned(space.id, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    if connector.get("kind") != "github":
        raise HTTPException(status_code=400, detail="The Decision Copilot works with GitHub connectors")
    return connector


# ── settings ────────────────────────────────────────────────────────────────

@router.get("/connectors/{connector_id}/copilot")
async def get_settings(connector_id: str, request: Request, space: Space = Depends(current_space),
                       session: ArcadeSession = Depends(get_session)) -> Row:
    connector = await _github_connector(session, space, connector_id)
    return {**copilot.settings(connector), "webhook_url": _webhook_url(request, connector_id)}


@router.put("/connectors/{connector_id}/copilot")
async def put_settings(connector_id: str, body: CopilotSettings, request: Request,
                       space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> Row:
    """Turning it on the first time (or rotating) creates the webhook secret, returned only in this response."""
    connector = await _github_connector(session, space, connector_id)
    stored = dict(connector.get("copilot") or {})
    stored["enabled"] = body.enabled
    secret = None
    if body.enabled and (body.rotate_secret or not stored.get("webhook_secret")):
        secret, stored["webhook_secret"] = copilot.new_secret(connector_id)
    await ConnectorRepository(session).update(space.id, connector_id, {"copilot": stored})
    await session.commit()
    out = {**copilot.settings({"copilot": stored}), "webhook_url": _webhook_url(request, connector_id)}
    if secret:
        out["webhook_secret"] = secret
    return out


# ── on demand ───────────────────────────────────────────────────────────────

@router.post("/knowledge/copilot/preview")
async def preview(body: PullRequestRef, space: Space = Depends(current_space),
                  session: ArcadeSession = Depends(get_session)) -> Row:
    connector = await _github_connector(session, space, body.connector_id)
    try:
        result = await copilot.analyze(session, space.id, connector, body.pr_number)
    except copilot.CopilotError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {k: v for k, v in result.items() if not k.startswith("_")}


@router.post("/knowledge/copilot/post")
async def post_now(body: PullRequestRef, space: Space = Depends(current_space),
                   session: ArcadeSession = Depends(get_session)) -> Row:
    """Post or update the comment, whether or not the analysis found it relevant: someone asked for it."""
    connector = await _github_connector(session, space, body.connector_id)
    result = await copilot.run(session, space.id, connector, body.pr_number, "manual", post=True)
    if result["status"] == "failed":
        raise HTTPException(status_code=502, detail=result["reason"])
    return result


@router.get("/knowledge/copilot/runs")
async def list_runs(space: Space = Depends(current_space), session: ArcadeSession = Depends(get_session)) -> List[Row]:
    return await copilot.runs(session, space.id)


# ── GitHub's webhook ────────────────────────────────────────────────────────

@router.post("/copilot/github/{connector_id}/webhook", status_code=202)
async def webhook(
    connector_id: str,
    request: Request,
    background: BackgroundTasks,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    """
    Signed with the connector's secret, or refused. Answers at once (GitHub gives
    up after 10 seconds) and analyses afterwards.
    """
    body = await request.body()
    connector = await session.query_one("SELECT FROM Connector WHERE id = :id", {"id": connector_id})
    # the same answer for an unknown connector and a bad signature: nothing to probe
    if not connector or not copilot.verify_signature(connector, body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")
    if not copilot.settings(connector)["enabled"]:
        return Response(status_code=204)
    if x_github_event == "ping":
        return Response(status_code=204)
    if x_github_event != "pull_request":
        return Response(status_code=204)
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Not JSON")
    pr = event.get("pull_request") or {}
    if event.get("action") not in copilot.PR_ACTIONS or pr.get("draft") or pr.get("state") != "open" \
            or not isinstance(pr.get("number"), int):
        return Response(status_code=204)
    await coordination.dispatch_copilot(connector["user_id"], connector_id, pr["number"],
                                        (pr.get("head") or {}).get("sha"), background)
    return Response(status_code=202)
