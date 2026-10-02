"""Connectors: configure external knowledge sources and sync them into the hub."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app import crypto
from app.arcadedb import ArcadeSession
from app.spaces import Space, current_space
from app.db import User, get_session
from app.rbac import Permission, has_permission
from app.repositories import ArtifactRepository, ConnectorRepository
from app.services import lineage
from app.services.connectors import REGISTRY
from app.services.connectors.base import (
    ConnectorError, checked_directory, filesystem_disabled_reason, validate_config,
)
from app import coordination
from app.services.connectors.sync import QueueUnavailable, is_running, secret_context, secret_names

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


def _denied_reason(user: User, module) -> Optional[str]:
    """Why this user can't use this kind of connector, or None."""
    if not module.FILESYSTEM:
        return None
    # filesystem connectors read the server's disk: an admin permission, and only inside CONNECTOR_ROOTS
    if not has_permission(user, Permission.USE_FILESYSTEM_CONNECTORS):
        return "Only administrators can use connectors that read the server's folders"
    return filesystem_disabled_reason()


def _check_allowed(user: User, module) -> None:
    reason = _denied_reason(user, module)
    if reason:
        raise HTTPException(status_code=403, detail=reason)


def _prepare_config(module, raw: Row, connector_id: str) -> Row:
    """Validate, check filesystem paths against CONNECTOR_ROOTS now (not only at sync), encrypt secrets."""
    try:
        config = validate_config(module.FIELDS, raw)
        if module.FILESYSTEM:
            checked_directory(config["path"])
        if hasattr(module, "check"):
            module.check(config)   # network connectors: the addresses the user gave are well-formed
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return crypto.seal_fields(config, secret_names(module), secret_context(connector_id))
    except crypto.CredentialsKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def _public(connector: Row, running: bool = False) -> Row:
    """API shape: secrets masked, sync state included. `running`: a sync holds the connector's lock."""
    module = REGISTRY.get(connector["kind"])
    secrets = set(secret_names(module)) if module else set()
    # stored secrets are ciphertext; neither that nor the plaintext ever leaves the server
    config = {k: (SECRET_MASK if k in secrets and v else v) for k, v in (connector.get("config") or {}).items()}
    return {
        "id": connector["id"],
        "kind": connector["kind"],
        "label": module.LABEL if module else connector["kind"],
        "name": connector.get("name"),
        "config": config,
        "created_at": connector.get("created_at"),
        "created_by": connector.get("created_by_name"),
        "last_sync_at": connector.get("last_sync_at"),
        # a "running" whose lock has expired means the process doing it died; with a worker, Celery
        # redelivers the task and it shows "running" again
        "last_status": "running" if running else (
            "interrupted" if connector.get("last_status") == "running" else connector.get("last_status")),
        "last_error": connector.get("last_error"),
        "last_result": connector.get("last_result") or {},
    }


async def _owned(session: ArcadeSession, space: Space, connector_id: str) -> Row:
    connector = await ConnectorRepository(session).get_owned(space.id, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    return connector


@router.get("/kinds")
async def connector_kinds(space: Space = Depends(current_space)) -> List[Row]:
    return [
        {"kind": m.KIND, "label": m.LABEL, "filesystem": m.FILESYSTEM,
         "allowed": _denied_reason(space.user, m) is None,
         "disabled_reason": _denied_reason(space.user, m),
         "needs_credentials_key": bool(secret_names(m)) and not crypto.available(),
         "fields": [f.describe() for f in m.FIELDS]}
        for m in REGISTRY.values()
    ]


@router.get("")
async def list_connectors(
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    queue_down = False

    async def running(connector_id: str) -> bool:
        # listing still works while the queue is down (sync state is then unknown);
        # after the first failure, don't wait on the queue again for every connector
        nonlocal queue_down
        if queue_down:
            return False
        try:
            return await is_running(connector_id)
        except QueueUnavailable:
            queue_down = True
            return False
    return [_public(c, await running(c["id"])) for c in await ConnectorRepository(session).list(space.id)]


@router.post("", status_code=201)
async def create_connector(
    body: ConnectorCreate,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    module = _module(body.kind)
    _check_allowed(space.user, module)
    if any(crypto.is_encrypted(body.config.get(n)) for n in secret_names(module)):
        raise HTTPException(status_code=400, detail="Credentials must be sent as plain values")
    connector_id = f"conn_{uuid.uuid4().hex[:16]}"
    config = _prepare_config(module, body.config, connector_id)
    connector = {
        "id": connector_id, "user_id": space.id, "created_by": space.user.id,
        "created_by_name": space.actor, "kind": body.kind,
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
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    connector = await _owned(session, space, connector_id)
    module = _module(connector["kind"])
    _check_allowed(space.user, module)
    fields: Row = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.config is not None:
        old = connector.get("config") or {}
        merged = dict(body.config)
        for name in secret_names(module):
            # a masked or omitted secret means "keep the stored (encrypted) one"
            if merged.get(name) in (None, "", SECRET_MASK):
                merged[name] = old.get(name)
            elif crypto.is_encrypted(merged[name]):
                # ciphertext can't be supplied by clients: it would bypass the field/record binding checks
                raise HTTPException(status_code=400, detail=f"Invalid value for {name}")
        fields["config"] = _prepare_config(module, merged, connector_id)
    if fields:
        await ConnectorRepository(session).update(space.id, connector_id, fields)
        await session.commit()
    return _public({**connector, **fields})


@router.delete("/{connector_id}", status_code=204)
async def delete_connector(
    connector_id: str,
    delete_artifacts: bool = False,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    """Remove the connector. With delete_artifacts=true, also delete everything it ingested."""
    await _owned(session, space, connector_id)
    if await is_running(connector_id):
        raise HTTPException(status_code=409, detail="A sync is running; try again when it finishes")
    repo = ConnectorRepository(session)
    if delete_artifacts:
        artifacts = ArtifactRepository(session)
        for artifact_id in await repo.artifact_ids(space.id, connector_id):
            await artifacts.delete_cascade(space.id, artifact_id)
        await lineage.refresh_statuses(session, space.id)
    await repo.delete(space.id, connector_id)
    await session.commit()
    return Response(status_code=204)


@router.post("/{connector_id}/sync", status_code=202)
async def sync_connector(
    connector_id: str,
    background: BackgroundTasks,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    """Start a sync in the background; poll GET /connectors for its status and result."""
    connector = await _owned(session, space, connector_id)
    _check_allowed(space.user, _module(connector["kind"]))
    if await is_running(connector_id):
        raise HTTPException(status_code=409, detail="A sync is already running")
    # "queued" until the worker (or this process) picks it up and marks it "running"
    await ConnectorRepository(session).update(space.id, connector_id, {"last_status": "queued", "last_error": None})
    await session.commit()
    await coordination.dispatch_sync(space.id, connector_id, space.user.id, background)
    return {**_public(connector), "last_status": "queued"}


@router.get("/{connector_id}/documents")
async def connector_documents(
    connector_id: str,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    await _owned(session, space, connector_id)
    return await ConnectorRepository(session).documents(space.id, connector_id)
