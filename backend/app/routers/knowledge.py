"""Knowledge CRUD, ingestion, review, search, linking, playbooks and graph view."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app.auth import get_current_user
from app.db import User, get_session
from app.dependencies import curation
from app.repositories import (
    ArtifactRepository,
    CrossLinkRepository,
    GraphStore,
    ItemEventRepository,
    KnowledgeItemRepository,
    PlaybookRepository,
    RelationshipRepository,
    SearchRepository,
)
from app.serializers import artifact_dict, item_dict, playbook_dict
from app.services import artifact_pipeline as pipeline
from app.services import lineage
from app.services.cross_source_linker import find_cross_links
from app.services.file_ingestion import extract_text_from_upload, fetch_url
from app.services.okf import export_okf_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

Row = Dict[str, Any]


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ArtifactRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    source: str = "manual"
    author: str = "unknown"
    tags: List[str] = []


class UrlIngestRequest(BaseModel):
    url: str
    title: str = Field(min_length=1, max_length=200)
    author: str = "unknown"
    tags: List[str] = []


class TranscriptRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    source_type: str = "transcript"  # transcript | email | slack
    author: str = "unknown"
    tags: List[str] = []


class ReviewDecision(BaseModel):
    status: str = Field(pattern="^(accepted|rejected)$")
    note: str = ""
    title: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class PlaybookRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    steps: List[Dict[str, Any]]


class ArtifactUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    content: Optional[str] = Field(default=None, min_length=1, max_length=100_000)
    tags: Optional[List[str]] = None


class ItemUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    tags: Optional[List[str]] = None
    details: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _owned_artifact(session: ArcadeSession, user: User, artifact_id: str) -> Row:
    artifact = await ArtifactRepository(session).get_owned(user.id, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


async def _owned_item(session: ArcadeSession, user: User, item_id: str) -> Row:
    item = await KnowledgeItemRepository(session).get_owned(user.id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


async def _update_item(
    session: ArcadeSession, user: User, item_id: str, fields: Row, event: Optional[Row] = None,
) -> Row:
    """Apply the changes and log them in the item's history (one transaction)."""
    repo = KnowledgeItemRepository(session)
    if fields:
        await repo.update(user.id, item_id, fields)
        if event:
            await ItemEventRepository(session).add(user.id, item_id, event.pop("kind"), event)
        await session.commit()
    return item_dict(await repo.get_owned(user.id, item_id))


# ---------------------------------------------------------------------------
# Read / export
# ---------------------------------------------------------------------------

@router.get("")
async def list_knowledge(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    user_id = current_user.id
    return {
        "artifacts": [artifact_dict(a) for a in await ArtifactRepository(session).list(user_id)],
        "knowledge_items": [item_dict(i) for i in await KnowledgeItemRepository(session).list(user_id)],
        "relationships": await RelationshipRepository(session).list(user_id),
        "playbooks": [playbook_dict(p) for p in await PlaybookRepository(session).list(user_id)],
    }


@router.post("/okf/import")
async def import_okf_payload(
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    return await pipeline.import_okf(session, current_user.id, payload)


@router.get("/okf/export")
async def export_okf(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    user_id = current_user.id
    return export_okf_payload(
        user_id,
        [artifact_dict(a) for a in await ArtifactRepository(session).list(user_id)],
        [item_dict(i) for i in await KnowledgeItemRepository(session).list(user_id)],
        await RelationshipRepository(session).list(user_id),
    )


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@router.post("/artifacts")
async def ingest_artifact(
    request: ArtifactRequest,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    return await pipeline.ingest_text(
        session, current_user.id, request.title, request.content,
        source=request.source, source_type="manual", author=request.author, tags=request.tags,
    )


@router.post("/artifacts/upload")
async def ingest_file(
    file: UploadFile = File(...),
    title: str = Form(...),
    author: str = Form("unknown"),
    tags: str = Form(""),
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    content = await extract_text_from_upload(file)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    return await pipeline.ingest_document(
        session, current_user.id, title, content,
        source="file", source_type="file", author=author, tags=tag_list,
    )


@router.post("/artifacts/url")
async def ingest_url(
    request: UrlIngestRequest,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    content = await fetch_url(request.url)
    return await pipeline.ingest_document(
        session, current_user.id, request.title, content,
        source=request.url, source_type="url", author=request.author, tags=request.tags,
    )


@router.post("/artifacts/transcript")
async def ingest_transcript(
    request: TranscriptRequest,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    return await pipeline.ingest_transcript(
        session, current_user.id, request.title, request.content,
        source_type=request.source_type, author=request.author, tags=request.tags,
    )


# ---------------------------------------------------------------------------
# Review workflow
# ---------------------------------------------------------------------------

@router.get("/review")
async def list_review_queue(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Dict[str, Any]]:
    items = await KnowledgeItemRepository(session).list(current_user.id, review_status="pending")
    return [item_dict(i) for i in items]


@router.patch("/review/{item_id}")
async def review_item(
    item_id: str,
    body: ReviewDecision,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    item = await _owned_item(session, current_user, item_id)
    fields: Row = {"review_status": body.status, "review_note": body.note}
    if body.title:
        fields["title"] = body.title
    if body.details is not None:
        fields["details"] = body.details
    event = {"kind": "reviewed", "from": item.get("review_status"), "to": body.status, "note": body.note}
    if body.title and body.title != item.get("title"):
        event["title"] = {"from": item.get("title"), "to": body.title}
    return await _update_item(session, current_user, item_id, fields, event)


# ---------------------------------------------------------------------------
# Cross-source linking
# ---------------------------------------------------------------------------

@router.post("/link")
async def run_cross_link(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    user_id = current_user.id
    items = await KnowledgeItemRepository(session).list(user_id)
    links = find_cross_links([{"id": i["id"], "artifact_id": i["artifact_id"], "title": i["title"]} for i in items])
    # stored as RELATED_TO edges; a re-run replaces the previous set
    await CrossLinkRepository(session).replace_all(user_id, links)
    await session.commit()
    created = [{"item_id_a": a, "item_id_b": b, "score": score} for a, b, score in links]
    return {"links_created": len(created), "links": created}


@router.get("/links")
async def get_cross_links(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Dict[str, Any]]:
    return [
        {"item_id_a": cl["item_id_a"], "item_id_b": cl["item_id_b"], "score": float(cl.get("score") or 0.0)}
        for cl in await CrossLinkRepository(session).list(current_user.id)
    ]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/search")
async def search_knowledge(
    q: str = "",
    type: Optional[str] = None,
    source_type: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    Full-text search: every word of `q` must match as a prefix of a word in the
    title, tags or type (items) / title, author, tags or content (artifacts);
    results are ranked by relevance, with artifacts matched only in their
    content ranked after title/author/tag matches and returned with a
    `snippet`. Each list holds at most `limit` rows; `total` counts all matches.
    """
    search = SearchRepository(session)
    items, item_total = await search.search_items(
        current_user.id, q, type=type, source_type=source_type, tag=tag, limit=limit
    )
    artifacts, artifact_total, snippets = await search.search_artifacts(
        current_user.id, q, source_type=source_type, tag=tag, limit=limit
    )
    return {
        "query": q,
        "filters": {"type": type, "source_type": source_type, "tag": tag},
        "knowledge_items": [item_dict(i) for i in items],
        "artifacts": [
            {**artifact_dict(a), **({"snippet": snippets[a["id"]]} if a["id"] in snippets else {})}
            for a in artifacts
        ],
        "total": item_total + artifact_total,
        "item_total": item_total,
        "artifact_total": artifact_total,
    }


# ---------------------------------------------------------------------------
# Playbooks
# ---------------------------------------------------------------------------

@router.post("/playbooks")
async def create_playbook(
    request: PlaybookRequest,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    playbook = curation.build_playbook(request.title, request.steps)
    await PlaybookRepository(session).add(current_user.id, playbook)
    await session.commit()
    return playbook


# ---------------------------------------------------------------------------
# Artifacts – update / delete
# ---------------------------------------------------------------------------

@router.put("/artifacts/{artifact_id}")
async def update_artifact(
    artifact_id: str,
    body: ArtifactUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    artifact = await _owned_artifact(session, current_user, artifact_id)
    changes: Row = {}
    if body.title is not None:
        changes["title"] = body.title
    if body.tags is not None:
        changes["tags"] = body.tags
    if body.content is not None:
        changes["content"] = body.content
    updated = {**artifact_dict(artifact), **changes}

    if body.content is not None:
        # re-extract items when content changes (this also saves title/tags)
        items = await pipeline.reextract_artifact(
            session, current_user.id, updated, artifact.get("metadata") or {}
        )
        return {**updated, "items": items}
    if changes:
        await ArtifactRepository(session).update(current_user.id, artifact_id, changes)
        await session.commit()
    return updated


@router.delete("/artifacts/{artifact_id}", status_code=204)
async def delete_artifact(
    artifact_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    await _owned_artifact(session, current_user, artifact_id)
    await ArtifactRepository(session).delete_cascade(current_user.id, artifact_id)
    # its items' lineage edges went with them, which can reactivate older decisions
    await lineage.refresh_statuses(session, current_user.id)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Knowledge items – read / update / delete
# ---------------------------------------------------------------------------

@router.get("/items/{item_id}")
async def get_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    return item_dict(await _owned_item(session, current_user, item_id))


@router.put("/items/{item_id}")
async def update_item(
    item_id: str,
    body: ItemUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    item = await _owned_item(session, current_user, item_id)
    fields: Row = {}
    if body.title is not None:
        fields["title"] = body.title
    if body.tags is not None:
        fields["tags"] = body.tags
    if body.details is not None:
        fields["details"] = body.details
    changed = sorted(k for k, v in fields.items() if v != item.get(k))
    event: Row = {"kind": "edited", "fields": changed}
    if "title" in changed:
        event["title"] = {"from": item.get("title"), "to": fields["title"]}
    return await _update_item(session, current_user, item_id, fields, event if changed else None)


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    await _owned_item(session, current_user, item_id)
    await KnowledgeItemRepository(session).delete(current_user.id, item_id)
    await lineage.refresh_statuses(session, current_user.id)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

@router.get("/graph")
async def knowledge_graph(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    return await GraphStore(session).visualization(current_user.id)


# Must stay last: the catch-all path would otherwise shadow the GET routes above.
@router.get("/{item_id}")
async def get_knowledge_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Get a knowledge item by id, id suffix, or title substring."""
    item = await KnowledgeItemRepository(session).find_by_ref(current_user.id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item_dict(item)
