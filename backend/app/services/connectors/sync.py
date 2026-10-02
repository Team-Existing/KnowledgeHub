"""
Run a connector: fetch its documents, ingest the new and changed ones, and
wire up lineage edges the source declares (ADR "Superseded by ...").

Each source document maps to one artifact with a fixed id, so re-syncs are
idempotent: unchanged documents (same content hash) are skipped without any
LLM call, and changed ones update their artifact in place — items whose text
didn't change keep their review state and lineage.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import crypto
from app import coordination
from app.arcadedb import ArcadeClient, ArcadeSession
from app.db import get_client
from app.repositories import ConnectorRepository, UserRepository
from app.services import artifact_pipeline as pipeline
from app.services import lineage
from app.services.connectors import REGISTRY
from app.services.connectors.base import ConnectorError, SourceDocument, validate_config
from app.services.providers import use_model

logger = logging.getLogger(__name__)

Row = Dict[str, Any]
MAX_REPORTED_ERRORS = 20

class QueueUnavailable(Exception):
    """Redis (worker mode) can't be reached, so sync state is unknown."""


async def is_running(connector_id: str) -> bool:
    """
    Whether a sync holds this connector's lock, in this or any other process
    (see app/coordination.py). Raises QueueUnavailable if that can't be known.
    """
    try:
        return await coordination.sync_running(connector_id)
    except Exception as exc:   # redis errors, timeouts
        raise QueueUnavailable(str(exc)) from exc


def secret_names(module) -> List[str]:
    return [f.name for f in module.FIELDS if f.secret]


def secret_context(connector_id: str) -> str:
    """Associated data binding a stored credential to its connector (the field name is appended)."""
    return f"connector:{connector_id}"


def decrypted_config(module, connector: Row) -> Row:
    """The connector's config with credentials decrypted — only ever held in memory for a sync."""
    try:
        config = crypto.open_fields(connector.get("config") or {}, secret_names(module), secret_context(connector["id"]))
    except crypto.DecryptionError as exc:
        raise ConnectorError(f"Stored credentials can't be used ({exc}). Re-enter them on the connector.")
    except crypto.CredentialsKeyMissing as exc:
        raise ConnectorError(str(exc))
    return validate_config(module.FIELDS, config)


def connector_artifact_id(user_id: str, connector_id: str, external_id: str) -> str:
    return pipeline.stable_id(user_id, "artifact", f"connector:{connector_id}:{external_id}")


def content_hash(doc: SourceDocument) -> str:
    payload = json.dumps([doc.mode, doc.title, doc.content, doc.entries, doc.tags], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


async def _ingest(session: ArcadeSession, user_id: str, connector: Row, doc: SourceDocument) -> Row:
    common = dict(
        artifact_id=connector_artifact_id(user_id, connector["id"], doc.external_id),
        created_at=doc.created_at,
    )
    tags = list(dict.fromkeys([connector["kind"], *doc.tags]))
    source = doc.url or f"{connector['kind']}:{doc.external_id}"
    if doc.mode == "structured":
        return await pipeline.ingest_structured(
            session, user_id, doc.title, doc.content, source=source, source_type=doc.source_type,
            author=doc.author, tags=tags, entries=doc.entries, **common)
    if doc.mode == "transcript":
        return await pipeline.ingest_transcript(
            session, user_id, doc.title, doc.content, source_type=doc.source_type,
            author=doc.author, tags=tags, source=source, **common)
    return await pipeline.ingest_document(
        session, user_id, doc.title, doc.content, source=source, source_type=doc.source_type,
        author=doc.author, tags=tags, **common)


async def _decision_of(session: ArcadeSession, user_id: str, artifact_id: str) -> Optional[str]:
    return await session.scalar(
        "SELECT id FROM KnowledgeItem WHERE artifact_id = :a AND user_id = :u AND type = 'decision' "
        "ORDER BY @rid LIMIT 1", {"a": artifact_id, "u": user_id},
    )


async def _apply_links(session: ArcadeSession, user_id: str, connector: Row, docs: List[SourceDocument]) -> int:
    created = 0
    for doc in docs:
        for link in doc.links:
            src = await _decision_of(session, user_id, connector_artifact_id(user_id, connector["id"], doc.external_id))
            dst = await _decision_of(session, user_id, connector_artifact_id(user_id, connector["id"], link["ref"]))
            if not src or not dst:
                continue
            try:
                await lineage.add_link(session, user_id, src, dst, link["kind"],
                                       note=f"from {connector['name']}", origin=connector["kind"],
                                       actor=connector.get("_actor"))
                created += 1
            except lineage.LineageError:
                await session.rollback()   # already linked on an earlier sync, or would be circular
    return created


async def run_sync(session: ArcadeSession, user_id: str, connector: Row) -> Row:
    """Fetch and ingest. Raises ConnectorError if the source can't be read at all."""
    module = REGISTRY[connector["kind"]]
    fetched = await module.fetch(decrypted_config(module, connector))

    repo = ConnectorRepository(session)
    known = await repo.document_hashes(user_id, connector["id"])
    result: Row = {"fetched": len(fetched.documents), "created": 0, "updated": 0, "unchanged": 0,
                   "failed": len(fetched.errors), "items_extracted": 0, "links_created": 0}
    errors: List[Row] = list(fetched.errors)

    for doc in fetched.documents:
        digest = content_hash(doc)
        if known.get(doc.external_id) == digest:
            result["unchanged"] += 1
            continue
        try:
            ingested = await _ingest(session, user_id, connector, doc)
            await repo.record_document(user_id, connector["id"], {
                "external_id": doc.external_id, "artifact_id": ingested["artifact"]["id"],
                "content_hash": digest, "title": doc.title, "url": doc.url,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            })
            await session.commit()
        except Exception as exc:   # one bad document must not stop the rest
            await session.rollback()
            logger.warning("Connector %s: %s failed: %s", connector["id"], doc.external_id, exc)
            result["failed"] += 1
            errors.append({"external_id": doc.external_id, "error": str(getattr(exc, "detail", exc))})
            continue
        result["updated" if doc.external_id in known else "created"] += 1
        result["items_extracted"] += ingested.get("extracted_count", 0)

    result["links_created"] = await _apply_links(session, user_id, connector, fetched.documents)
    result["errors"] = errors[:MAX_REPORTED_ERRORS]
    return result


async def run_sync_job(user_id: str, connector_id: str, actor_id: Optional[str] = None,
                       client: Optional[ArcadeClient] = None) -> None:
    """
    Run one sync: own session, records progress and outcome on the connector.
    `user_id` is the space the connector belongs to; `actor_id` the person who
    started the sync (their chosen LLM is used, and lineage links name them).
    Runs in the API process (in-process mode) or on the Celery worker, which
    passes its own `client`. The connector's lock makes a second, concurrent
    run of the same connector a no-op, across all processes.
    """
    async with coordination.held_lock(coordination.sync_lock(connector_id), coordination.SYNC_LOCK_TTL) as token:
        if token is None:
            logger.info("Connector %s is already syncing elsewhere; skipping", connector_id)
            return
        await _run_sync_locked(user_id, connector_id, actor_id, client or get_client())


async def _run_sync_locked(user_id: str, connector_id: str, actor_id: Optional[str], client: ArcadeClient) -> None:
    session = ArcadeSession(client)
    repo = ConnectorRepository(session)
    try:
        connector = await repo.get_owned(user_id, connector_id)
        if not connector:
            return
        # runs outside the request, so apply the user's chosen LLM here explicitly
        actor = await UserRepository(session).get(actor_id or user_id)
        use_model((actor or {}).get("llm_model"))
        connector = {**connector, "_actor": (actor or {}).get("username")}
        await repo.update(user_id, connector_id, {"last_status": "running", "last_error": None})
        await session.commit()
        try:
            result = await run_sync(session, user_id, connector)
            status, error = ("partial" if result["failed"] else "ok"), None
        except ConnectorError as exc:
            await session.rollback()
            result, status, error = {}, "error", str(exc)
        except Exception as exc:
            await session.rollback()
            logger.exception("Connector %s sync crashed", connector_id)
            result, status, error = {}, "error", f"Unexpected error: {exc}"
        await repo.update(user_id, connector_id, {
            "last_status": status, "last_error": error, "last_result": result,
            "last_sync_at": datetime.now(timezone.utc).isoformat(),
        })
        await session.commit()
    finally:
        await session.close()
