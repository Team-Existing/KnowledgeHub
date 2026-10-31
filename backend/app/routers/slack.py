"""
Slack's /decisions command (services/slack.py).

  GET/POST   /knowledge/slack          the space's Slack apps (name, request URL); add one with its signing secret
  PUT/DELETE /knowledge/slack/{id}     replace the signing secret, or remove it
  POST       /slack/{id}/command       Slack's slash-command requests (public, signed)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import coordination
from app.arcadedb import ArcadeSession
from app.db import get_session
from app.services import slack
from app.spaces import Space, current_space

router = APIRouter(tags=["slack"])

Row = Dict[str, Any]


class SlackCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    signing_secret: str = Field(min_length=16, max_length=128)    # Slack app → Basic Information → Signing Secret


class SlackSecret(BaseModel):
    signing_secret: str = Field(min_length=16, max_length=128)


def _request_url(request: Request, integration_id: str) -> str:
    public = (os.getenv("PUBLIC_URL", "").strip() or str(request.base_url)).rstrip("/")
    return f"{public}/slack/{integration_id}/command"


@router.get("/knowledge/slack")
async def list_slack(request: Request, space: Space = Depends(current_space),
                     session: ArcadeSession = Depends(get_session)) -> List[Row]:
    return [{**i, "request_url": _request_url(request, i["id"]), "command": slack.COMMAND}
            for i in await slack.list_integrations(session, space.id)]


@router.post("/knowledge/slack", status_code=201)
async def add_slack(body: SlackCreate, request: Request, space: Space = Depends(current_space),
                    session: ArcadeSession = Depends(get_session)) -> Row:
    row = await slack.create(session, space.id, body.name.strip(), body.signing_secret.strip(),
                             space.user.id, space.actor)
    return {**row, "request_url": _request_url(request, row["id"]), "command": slack.COMMAND}


@router.put("/knowledge/slack/{integration_id}", status_code=204)
async def replace_secret(integration_id: str, body: SlackSecret, space: Space = Depends(current_space),
                         session: ArcadeSession = Depends(get_session)) -> Response:
    if not await slack.set_secret(session, space.id, integration_id, body.signing_secret.strip()):
        raise HTTPException(status_code=404, detail="Slack app not found")
    return Response(status_code=204)


@router.delete("/knowledge/slack/{integration_id}", status_code=204)
async def remove_slack(integration_id: str, space: Space = Depends(current_space),
                       session: ArcadeSession = Depends(get_session)) -> Response:
    if not await slack.delete(session, space.id, integration_id):
        raise HTTPException(status_code=404, detail="Slack app not found")
    return Response(status_code=204)


def _ephemeral(text: str) -> JSONResponse:
    return JSONResponse({"response_type": "ephemeral", "text": text})


@router.post("/slack/{integration_id}/command")
async def command(integration_id: str, request: Request, background: BackgroundTasks,
                  session: ArcadeSession = Depends(get_session)) -> Response:
    """
    Verified with the app's signing secret, or refused. Slack waits at most three
    seconds, so this acknowledges at once and answers through response_url afterwards.
    """
    body = await request.body()
    integration = await session.query_one(
        "SELECT id, user_id, signing_secret FROM SlackIntegration WHERE id = :id", {"id": integration_id})
    # the same answer for an unknown integration and a bad signature: nothing to probe
    if not integration or not slack.verify(integration, body, request.headers.get("x-slack-request-timestamp", ""),
                                           request.headers.get("x-slack-signature", "")):
        raise HTTPException(status_code=401, detail="Invalid signature")
    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
    if form.get("ssl_check"):
        return Response(status_code=200)
    text = (form.get("text") or "").strip()
    change, _ = slack.parse(text)
    if not change or change.lower() == "help":
        return _ephemeral(slack.HELP)
    if not slack.response_url_ok(form.get("response_url", "")):
        return _ephemeral("Slack didn't send a reply address this app can use.")
    await coordination.dispatch_slack(integration["user_id"], integration_id, text, form["response_url"],
                                      form.get("user_name") or "someone", background)
    return _ephemeral(f"Looking up what the record says about: _{change[:200]}_ …")
