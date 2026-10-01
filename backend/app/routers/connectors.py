"""Connectors: configure external knowledge sources and sync them into the hub."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app.auth import get_current_user
from app.db import User, get_session
from app.repositories import ArtifactRepository, ConnectorRepository
from app.services import lineage
from app.services.connectors import REGISTRY
from app.services.connectors.base import ConnectorError, validate_config
from app.services.connectors.sync import is_running, run_sync_job

router = APIRouter(prefix="/connectors", tags=["connectors"])

Row = Dict[str, Any]
SECRET_MASK = "********"


class ConnectorCreate(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=120)
    config: Dict[str, Any] = {}


class ConnectorUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    config: Optional[Dict[str, Any]] = None


def _module(kind: str):
    module = REGISTRY.get(kind)
    if not module:
        raise HTTPException(status_code=400, detail=f"Unknown connector kind '{kind}'. Use one of: {', '.join(REGISTRY)}")
    return module


def _check_allowed(user: User, module) -> None:
    # filesystem connectors read the server's disk, so only admins may point them anywhere
    if module.FILESYSTEM and user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can add connectors that read local folders")


def _public(connector: Row) -> Row:
    """API shape: secrets masked, sync state included."""
    module = REGISTRY.get(connector["kind"])
    secrets = {f.name for f in module.FIELDS if f.secret} if module else set()
    config = {k: (SECRET_MASK if k in secrets and v else v) for k, v in (connector.get("config") or {}).items()}
    running = is_running(connector["id"])
    return {
        "id": connector["id"],
        "kind": connector["kind"],
        "label": module.LABEL if module else connector["kind"],
        "name": connector.get("name"),
        "config": config,
        "created_at": connector.get("created_at"),
        "last_sync_at": connector.get("last_sync_at"),
        # a "running" left behind by a server restart is not really running
        "last_status": "running" if running else (
            "interrupted" if connector.get("last_status") == "running" else connector.get("last_status")),
        "last_error": connector.get("last_error"),
        "last_result": connector.get("last_result") or {},
    }


async def _owned(session: ArcadeSession, user: User, connector_id: str) -> Row:
    connector = await ConnectorRepository(session).get_owned(user.id, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    return connector


@router.get("/kinds")
async def connector_kinds(current_user: User = Depends(get_current_user)) -> List[Row]:
    return [
        {"kind": m.KIND, "label": m.LABEL, "filesystem": m.FILESYSTEM,
         "allowed": not m.FILESYSTEM or current_user.role == "admin",
         "fields": [f.describe() for f in m.FIELDS]}
        for m in REGISTRY.values()
    ]


@router.get("")
async def list_connectors(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    return [_public(c) for c in await ConnectorRepository(session).list(current_user.id)]


@router.post("", status_code=201)
async def create_connector(
    body: ConnectorCreate,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    module = _module(body.kind)
    _check_allowed(current_user, module)
    try:
        config = validate_config(module.FIELDS, body.config)
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    connector = {
        "id": f"conn_{uuid.uuid4().hex[:16]}", "user_id": current_user.id, "kind": body.kind,
        "name": body.name, "config": config, "created_at": datetime.now(timezone.utc).isoformat(),
        "last_status": "never", "last_result": {},
    }
    await ConnectorRepository(session).add(connector)
    await session.commit()
    return _public(connector)


@router.patch("/{connector_id}")
async def update_connector(
    connector_id: str,
    body: ConnectorUpdate,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    connector = await _owned(session, current_user, connector_id)
    module = _module(connector["kind"])
    _check_allowed(current_user, module)
    fields: Row = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.config is not None:
        old = connector.get("config") or {}
        merged = dict(body.config)
        for f in module.FIELDS:
            # a masked or omitted secret means "keep the stored one"
            if f.secret and merged.get(f.name) in (None, "", SECRET_MASK):
                merged[f.name] = old.get(f.name)
        try:
            fields["config"] = validate_config(module.FIELDS, merged)
        except ConnectorError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if fields:
        await ConnectorRepository(session).update(current_user.id, connector_id, fields)
        await session.commit()
    return _public({**connector, **fields})


@router.delete("/{connector_id}", status_code=204)
async def delete_connector(
    connector_id: str,
    delete_artifacts: bool = False,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    """Remove the connector. With delete_artifacts=true, also delete everything it ingested."""
    await _owned(session, current_user, connector_id)
    if is_running(connector_id):
        raise HTTPException(status_code=409, detail="A sync is running; try again when it finishes")
    repo = ConnectorRepository(session)
    if delete_artifacts:
        artifacts = ArtifactRepository(session)
        for artifact_id in await repo.artifact_ids(current_user.id, connector_id):
            await artifacts.delete_cascade(current_user.id, artifact_id)
        await lineage.refresh_statuses(session, current_user.id)
    await repo.delete(current_user.id, connector_id)
    await session.commit()
    return Response(status_code=204)


@router.post("/{connector_id}/sync", status_code=202)
async def sync_connector(
    connector_id: str,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    """Start a sync in the background; poll GET /connectors for its status and result."""
    connector = await _owned(session, current_user, connector_id)
    _check_allowed(current_user, _module(connector["kind"]))
    if is_running(connector_id):
        raise HTTPException(status_code=409, detail="A sync is already running")
    background.add_task(run_sync_job, current_user.id, connector_id)
    return {**_public(connector), "last_status": "running"}


@router.get("/{connector_id}/documents")
async def connector_documents(
    connector_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    await _owned(session, current_user, connector_id)
    return await ConnectorRepository(session).documents(current_user.id, connector_id)
