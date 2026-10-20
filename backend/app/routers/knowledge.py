"""Knowledge CRUD, ingestion, review, search, linking, playbooks and graph view."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app.spaces import Space, current_space, resolve_space
from app.db import get_session
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


class ShareRequest(BaseModel):
    space_id: str = Field(min_length=1)   # a space you belong to: a group's id, or your own user id


class ItemUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    tags: Optional[List[str]] = None
    details: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _owned_artifact(session: ArcadeSession, space: Space, artifact_id: str) -> Row:
    artifact = await ArtifactRepository(session).get_owned(space.id, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


async def _owned_item(session: ArcadeSession, space: Space, item_id: str) -> Row:
    item = await KnowledgeItemRepository(session).get_owned(space.id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


async def _update_item(
    session: ArcadeSession, space: Space, item_id: str, fields: Row, event: Optional[Row] = None,
) -> Row:
    """Apply the changes and log them in the item's history (one transaction)."""
    repo = KnowledgeItemRepository(session)
    if fields:
        await repo.update(space.id, item_id, fields)
        if event:
            await ItemEventRepository(session).add(space.id, item_id, event.pop("kind"), event, actor=space.actor)
        await session.commit()
    return item_dict(await repo.get_owned(space.id, item_id))


# ---------------------------------------------------------------------------
# Read / export
# ---------------------------------------------------------------------------

# A page of a space is bounded: a large space returned everything at once (4 MB for
# 3,000 items), which made each Hub load take seconds and, under load, ran the
# database out of memory.
PAGE_DEFAULT = 100
PAGE_MAX = 1000


@router.get("")
async def list_knowledge(
    limit: int = Query(default=PAGE_DEFAULT, ge=1, le=PAGE_MAX),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
    type: Optional[str] = None,
    include_content: bool = False,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    The space, a page at a time: the newest `limit` artifacts and items after
    `offset` (items filtered by `q`, matching title or tags, and `type`), the
    relationships between the returned nodes, every playbook, and `totals`.
    Artifacts come without their text unless `include_content` is set.
    """
    user_id = space.id
    items_repo, artifacts_repo = KnowledgeItemRepository(session), ArtifactRepository(session)
    artifacts = await artifacts_repo.list(user_id, with_content=include_content,
                                          limit=limit, offset=offset, newest_first=True)
    items = await items_repo.list(user_id, q=q, type=type, limit=limit, offset=offset, newest_first=True)
    types = await session.query("SELECT DISTINCT(type) AS type FROM KnowledgeItem WHERE user_id = :u", {"u": user_id})
    return {
        "artifacts": [artifact_dict(a, content=include_content) for a in artifacts],
        "knowledge_items": [item_dict(i) for i in items],
        "relationships": await RelationshipRepository(session).among(
            user_id, [i["id"] for i in items], [a["id"] for a in artifacts]),
        "playbooks": [playbook_dict(p) for p in await PlaybookRepository(session).list(user_id)],
        "totals": {
            "artifacts": await artifacts_repo.count(user_id),
            "knowledge_items": await items_repo.count(user_id),
            "matching_items": await items_repo.count(user_id, q=q, type=type),
            "pending_review": await items_repo.count(user_id, review_status="pending"),
        },
        "item_types": sorted({r["type"] for r in types if r.get("type")}),
    }


@router.post("/okf/import")
async def import_okf_payload(
    payload: Dict[str, Any],
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    return await pipeline.import_okf(session, space.id, payload)


@router.get("/okf/export")
async def export_okf(
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    user_id = space.id
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
    background: BackgroundTasks,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    return await pipeline.ingest_text(
        session, space.id, request.title, request.content,
        source=request.source, source_type="manual", author=request.author, tags=request.tags,
        background=background,
    )


@router.post("/artifacts/upload")
async def ingest_file(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(...),
    author: str = Form("unknown"),
    tags: str = Form(""),
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    content = await extract_text_from_upload(file)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    return await pipeline.ingest_document(
        session, space.id, title, content,
        source="file", source_type="file", author=author, tags=tag_list, background=background,
    )


@router.post("/artifacts/url")
async def ingest_url(
    request: UrlIngestRequest,
    background: BackgroundTasks,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    content = await fetch_url(request.url)
    return await pipeline.ingest_document(
        session, space.id, request.title, content,
        source=request.url, source_type="url", author=request.author, tags=request.tags,
        background=background,
    )


@router.post("/artifacts/transcript")
async def ingest_transcript(
    request: TranscriptRequest,
    background: BackgroundTasks,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    return await pipeline.ingest_transcript(
        session, space.id, request.title, request.content,
        source_type=request.source_type, author=request.author, tags=request.tags,
        background=background,
    )


# ---------------------------------------------------------------------------
# Review workflow
# ---------------------------------------------------------------------------

@router.get("/review")
async def list_review_queue(
    response: Response,
    limit: int = Query(default=50, ge=1, le=PAGE_MAX),
    offset: int = Query(default=0, ge=0),
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Pending items, oldest first, a page at a time; X-Total-Count holds the length of the queue."""
    repo = KnowledgeItemRepository(session)
    items = await repo.list(space.id, review_status="pending", limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(await repo.count(space.id, review_status="pending"))
    return [item_dict(i) for i in items]


@router.patch("/review/{item_id}")
async def review_item(
    item_id: str,
    body: ReviewDecision,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    item = await _owned_item(session, space, item_id)
    fields: Row = {"review_status": body.status, "review_note": body.note}
    if body.title:
        fields["title"] = body.title
    if body.details is not None:
        fields["details"] = body.details
    event = {"kind": "reviewed", "from": item.get("review_status"), "to": body.status, "note": body.note}
    if body.title and body.title != item.get("title"):
        event["title"] = {"from": item.get("title"), "to": body.title}
    return await _update_item(session, space, item_id, fields, event)


# ---------------------------------------------------------------------------
# Cross-source linking
# ---------------------------------------------------------------------------

@router.post("/link")
async def run_cross_link(
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    user_id = space.id
    items = await KnowledgeItemRepository(session).list(user_id)
    links = find_cross_links([{"id": i["id"], "artifact_id": i["artifact_id"], "title": i["title"]} for i in items])
    # stored as RELATED_TO edges; a re-run replaces the previous set
    await CrossLinkRepository(session).replace_all(user_id, links)
    await session.commit()
    created = [{"item_id_a": a, "item_id_b": b, "score": score} for a, b, score in links]
    return {"links_created": len(created), "links": created}


@router.get("/links")
async def get_cross_links(
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> List[Dict[str, Any]]:
    return [
        {"item_id_a": cl["item_id_a"], "item_id_b": cl["item_id_b"], "score": float(cl.get("score") or 0.0)}
        for cl in await CrossLinkRepository(session).list(space.id)
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
    limit: int = Query(default=50, ge=1, le=500),
    space: Space = Depends(current_space),
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
        space.id, q, type=type, source_type=source_type, tag=tag, limit=limit
    )
    artifacts, artifact_total, snippets = await search.search_artifacts(
        space.id, q, source_type=source_type, tag=tag, limit=limit
    )
    # sources have no page of their own: link each to an item extracted from it, whose
    # page shows the source text (artifact results used to be dead ends)
    first_items: Dict[str, str] = {}
    if artifacts:
        for row in await session.query(
            "SELECT artifact_id, min(id) AS id FROM KnowledgeItem WHERE user_id = :u AND artifact_id IN :a "
            "GROUP BY artifact_id", {"u": space.id, "a": [a["id"] for a in artifacts]},
        ):
            first_items[row["artifact_id"]] = row["id"]
    return {
        "query": q,
        "filters": {"type": type, "source_type": source_type, "tag": tag},
        "knowledge_items": [item_dict(i) for i in items],
        # without their text: a matching passage comes back as `snippet`
        "artifacts": [
            {**artifact_dict(a, content=False),
             **({"snippet": snippets[a["id"]]} if a["id"] in snippets else {}),
             **({"first_item_id": first_items[a["id"]]} if a["id"] in first_items else {})}
            for a in artifacts
        ],
        "total": item_total + artifact_total,
        "item_total": item_total,
        "artifact_total": artifact_total,
    }


# ---------------------------------------------------------------------------
# Playbooks
# ---------------------------------------------------------------------------

@router.get("/playbooks")
async def list_playbooks(
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> List[Dict[str, Any]]:
    return [playbook_dict(p) for p in await PlaybookRepository(session).list(space.id)]


@router.delete("/playbooks/{playbook_id}", status_code=204)
async def delete_playbook(
    playbook_id: str,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    if not await PlaybookRepository(session).delete(space.id, playbook_id):
        raise HTTPException(status_code=404, detail="Playbook not found")
    await session.commit()
    return Response(status_code=204)


@router.post("/playbooks")
async def create_playbook(
    request: PlaybookRequest,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    playbook = curation.build_playbook(space.id, request.title, request.steps)
    if not playbook["steps"]:
        raise HTTPException(status_code=400, detail="A playbook needs at least one step with text")
    await PlaybookRepository(session).add(space.id, playbook)
    await session.commit()
    return playbook


# ---------------------------------------------------------------------------
# Artifacts – update / delete
# ---------------------------------------------------------------------------

@router.put("/artifacts/{artifact_id}")
async def update_artifact(
    artifact_id: str,
    body: ArtifactUpdateRequest,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    artifact = await _owned_artifact(session, space, artifact_id)
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
            session, space.id, updated, artifact.get("metadata") or {}
        )
        return {**updated, "items": items}
    if changes:
        await ArtifactRepository(session).update(space.id, artifact_id, changes)
        await session.commit()
    return updated


@router.post("/artifacts/{artifact_id}/share")
async def share_artifact(
    artifact_id: str,
    body: ShareRequest,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Copy a source (with its knowledge items) from the active space into another space you belong to."""
    await _owned_artifact(session, space, artifact_id)
    target = await resolve_space(session, space.user, body.space_id)   # 403 unless you're a member
    if target.id == space.id:
        raise HTTPException(status_code=400, detail="That source is already in this space")
    result = await pipeline.copy_artifact(session, space.id, target.id, artifact_id, shared_by=space.actor)
    return {**result, "space_id": target.id, "space_name": target.name}


@router.delete("/artifacts/{artifact_id}", status_code=204)
async def delete_artifact(
    artifact_id: str,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    await _owned_artifact(session, space, artifact_id)
    await ArtifactRepository(session).delete_cascade(space.id, artifact_id)
    # its items' lineage edges went with them, which can reactivate older decisions
    await lineage.refresh_statuses(session, space.id)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Knowledge items – read / update / delete
# ---------------------------------------------------------------------------

@router.get("/items")
async def list_items(
    q: Optional[str] = Query(default=None, max_length=200),
    type: List[str] = Query(default=[]),
    exclude_rejected: bool = False,
    limit: int = Query(default=PAGE_DEFAULT, ge=1, le=PAGE_MAX),
    offset: int = Query(default=0, ge=0),
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Items newest first, filtered by `q` (title or tags) and any of `type`, with the total that match."""
    repo = KnowledgeItemRepository(session)
    filters: Row = {"q": q, "types": type or None, "exclude_rejected": exclude_rejected}
    items = await repo.list(space.id, **filters, limit=limit, offset=offset, newest_first=True)
    return {"items": [item_dict(i) for i in items], "total": await repo.count(space.id, **filters)}


@router.get("/items/{item_id}/context")
async def item_context(
    item_id: str,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """What the page of an item shows around it: its source (with text), its relationships, their titles."""
    item = await _owned_item(session, space, item_id)
    artifacts = ArtifactRepository(session)
    artifact = await artifacts.get_owned(space.id, item["artifact_id"]) if item.get("artifact_id") else None
    relationships = await RelationshipRepository(session).touching(space.id, [item_id])
    other_ids = sorted({r[end] for r in relationships for end in ("from", "to")} - {item_id})
    nodes = {i["id"]: {"title": i.get("title"), "kind": "item", "type": i.get("type")}
             for i in await KnowledgeItemRepository(session).get_many(space.id, other_ids)}
    for aid in [oid for oid in other_ids if oid not in nodes]:
        a = await artifacts.get_owned(space.id, aid)
        if a:
            nodes[aid] = {"title": a.get("title"), "kind": "artifact", "type": "artifact"}
    return {
        "item": item_dict(item),
        "artifact": artifact_dict(artifact) if artifact else None,
        "relationships": relationships,
        "nodes": nodes,
    }


@router.get("/items/{item_id}")
async def get_item(
    item_id: str,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    return item_dict(await _owned_item(session, space, item_id))


@router.put("/items/{item_id}")
async def update_item(
    item_id: str,
    body: ItemUpdateRequest,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    item = await _owned_item(session, space, item_id)
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
    return await _update_item(session, space, item_id, fields, event if changed else None)


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(
    item_id: str,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    await _owned_item(session, space, item_id)
    await KnowledgeItemRepository(session).delete(space.id, item_id)
    await lineage.refresh_statuses(session, space.id)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

@router.get("/graph")
async def knowledge_graph(
    limit: int = Query(default=300, ge=1, le=PAGE_MAX),
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """The newest `limit` nodes and the edges between them; `truncated` says whether there are more."""
    return await GraphStore(session).visualization(space.id, limit=limit)


# Must stay last: the catch-all path would otherwise shadow the GET routes above.
@router.get("/{item_id}")
async def get_knowledge_item(
    item_id: str,
    space: Space = Depends(current_space),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Get a knowledge item by id, id suffix, or title substring."""
    item = await KnowledgeItemRepository(session).find_by_ref(space.id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item_dict(item)
