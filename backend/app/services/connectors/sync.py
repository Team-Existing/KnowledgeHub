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
from typing import Any, Dict, List, Optional, Set

from app.arcadedb import ArcadeSession
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

# connector ids with a sync in progress in this process
_running: Set[str] = set()


def is_running(connector_id: str) -> bool:
    return connector_id in _running


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
                                       note=f"from {connector['name']}", origin=connector["kind"])
                created += 1
            except lineage.LineageError:
                await session.rollback()   # already linked on an earlier sync, or would be circular
    return created


async def run_sync(session: ArcadeSession, user_id: str, connector: Row) -> Row:
    """Fetch and ingest. Raises ConnectorError if the source can't be read at all."""
    module = REGISTRY[connector["kind"]]
    config = validate_config(module.FIELDS, connector.get("config") or {})
    fetched = await module.fetch(config)

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


async def run_sync_job(user_id: str, connector_id: str) -> None:
    """Background task: own session, records progress and outcome on the connector."""
    if connector_id in _running:
        return
    _running.add(connector_id)
    session = ArcadeSession(get_client())
    repo = ConnectorRepository(session)
    try:
        connector = await repo.get_owned(user_id, connector_id)
        if not connector:
            return
        # runs outside the request, so apply the user's chosen LLM here explicitly
        owner = await UserRepository(session).get(user_id)
        use_model((owner or {}).get("llm_model"))
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
        _running.discard(connector_id)
        await session.close()
