"""
Artifact ingestion pipeline: extract items → persist (artifact + item vertices
and CONTAINS edges in ArcadeDB) → index (embeddings, summary). Every ingestion
route — manual text, file upload, URL, transcript, OKF import, artifact edit —
goes through persist_artifact() and index_artifact(); they differ only in how
the item list is built.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.arcadedb import ArcadeSession
from app.dependencies import extractor, ingestion
from app.repositories import (
    ArtifactRepository,
    KnowledgeItemRepository,
    RelationshipRepository,
)
from app.serializers import item_dict
from app.services import lineage
from app.services.graphrag import embed, embed_items
from app.services.item_schema import normalize_item_details
from app.services.llm_extraction import _summarise_text, extract_from_document, extract_from_transcript
from app.services.okf import normalize_okf_payload
from app.schema import record_embedding_model
from app.services.providers import get_embedding_provider

Item = Dict[str, Any]


def stable_id(user_id: str, prefix: str, value: str) -> str:
    # user_id is part of the hash so identical content from different users
    # never collides on the same node.
    digest = hashlib.sha256(f"{user_id}:{value}".encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _item(
    user_id: str, prefix: str, artifact_id: str, title: str, type: str,
    author: str, created_at: str, tags: List[str], details: Dict[str, Any],
) -> Item:
    # the id hashes the full title; only the stored title is truncated
    return {
        "id": stable_id(user_id, prefix, f"{artifact_id}:{title}"),
        "artifact_id": artifact_id,
        "title": title[:180],
        "type": type,
        "author": author,
        "date": created_at,
        "tags": tags,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Item builders
# ---------------------------------------------------------------------------

def build_regex_items(
    user_id: str, artifact_id: str, content: str, author: str, tags: List[str], created_at: str,
) -> List[Item]:
    extracted = {
        "decisions": extractor.extract_decisions(content),
        "how_tos": extractor.mine_how_to_patterns(content),
        "best_practices": extractor.identify_best_practices(content),
        "lessons": extractor.extract_lessons_learned(content),
        "risks": extractor.recognize_risk_patterns(content),
    }
    items: List[Item] = []
    for label in ["decisions", "how_tos", "lessons", "risks"]:
        for entry in extracted[label]:
            title = entry.get("what")  # canonical key after normalization
            if title:
                items.append(_item(user_id, label, artifact_id, title, label.rstrip("s").replace("_", "-"),
                                   author, created_at, tags, entry))
    for practice in extracted["best_practices"]:
        items.append(_item(user_id, "practice", artifact_id, practice, "best-practice",
                           author, created_at, tags, {"practice": practice}))
    return items + build_checklist_items(user_id, artifact_id, content, author, tags, created_at)


def build_checklist_items(
    user_id: str, artifact_id: str, content: str, author: str, tags: List[str], created_at: str,
) -> List[Item]:
    """Explicit "[ ]" / "TODO:" lines — precise enough to keep regex-only on every path."""
    return [
        _item(user_id, "checklist", artifact_id, checklist, "checklist",
              author, created_at, tags, {"item": checklist})
        for checklist in extractor.detect_checklists(content)
    ]


# (llm_result key, id prefix, item type, use entry's "who" as author)
_TRANSCRIPT_KINDS = [
    ("decisions", "decision", "decision", True),
    ("action_items", "action", "action-item", True),
    ("risks", "risk", "risk", False),
]
_DOCUMENT_KINDS = _TRANSCRIPT_KINDS + [
    ("how_tos", "howto", "how-to", False),
    ("best_practices", "practice", "best-practice", False),
    ("lessons", "lesson", "lesson", False),
]


def build_llm_items(
    user_id: str, artifact_id: str, llm_result: Dict[str, Any],
    author: str, tags: List[str], created_at: str,
    kinds: List[Tuple[str, str, str, bool]] = _TRANSCRIPT_KINDS,
) -> List[Item]:
    """Items from LLM output — details are already normalized by _normalize_llm_result."""
    items: List[Item] = []
    for key, prefix, type_, use_who in kinds:
        for entry in llm_result.get(key, []):
            title = entry.get("what", "").strip()[:180]
            if title:
                item_author = (entry.get("who") or author) if use_who else author
                items.append(_item(user_id, prefix, artifact_id, title, type_,
                                   item_author, created_at, tags, entry))
    return items


# ---------------------------------------------------------------------------
# Persist + index
# ---------------------------------------------------------------------------

async def persist_artifact(
    session: ArcadeSession,
    user_id: str,
    artifact: Dict[str, Any],
    items: List[Item],
    metadata: Dict[str, Any],
    extraction_engine: str = "regex",
) -> List[Dict[str, Any]]:
    """Upsert the artifact, sync its items, ensure CONTAINS edges — one transaction. Returns the edges."""
    await ArtifactRepository(session).upsert(
        user_id,
        artifact["id"],
        title=artifact["title"],
        content=artifact["content"],
        source=artifact["source"],
        source_type=artifact["source_type"],
        author=artifact["author"],
        tags=artifact["tags"],
        created_at=artifact["created_at"],
        metadata=metadata,
        extraction_engine=extraction_engine,
    )
    await KnowledgeItemRepository(session).sync_for_artifact(
        user_id, artifact["id"], items, extraction_engine
    )
    relationships = await RelationshipRepository(session).ensure_contains(
        user_id, artifact["id"], [i["id"] for i in items]
    )
    # new decisions/risks get their first status; removed items may have freed others
    await lineage.refresh_statuses(session, user_id)
    await session.commit()
    return relationships


async def index_artifact(
    session: ArcadeSession,
    user_id: str,
    artifact: Dict[str, Any],
    items: List[Item],
    summarise: bool = True,
) -> None:
    """Embed items and (optionally) rebuild the artifact summary."""
    await embed_and_store(session, user_id, items)
    if summarise:
        await build_artifact_summary(session, user_id, artifact["id"], artifact["content"])


async def embed_and_store(session: ArcadeSession, user_id: str, items: List[Item]) -> int:
    """
    Embed items and store the vectors (indexed by the KnowledgeItem[embedding]
    vector index). Provenance (embedding_provider, embedding_dims) is kept so a
    model switch can be detected. Returns the number of items updated.
    """
    pairs = await embed_items(items)
    if not pairs:
        return 0
    provider = get_embedding_provider()
    repo = KnowledgeItemRepository(session)
    updated = 0
    for item_id, vector in pairs:
        if await repo.set_embedding(user_id, item_id, vector, provider.name, provider.dimensions):
            updated += 1
    await session.commit()
    return updated


async def build_artifact_summary(
    session: ArcadeSession, user_id: str, artifact_id: str, content: str,
) -> None:
    """Generate and store a condensed LLM summary + its embedding on the artifact (summary index)."""
    summary_text = await _summarise_text(content)
    if not summary_text:
        return
    summary_vec = await embed(summary_text)
    provider = get_embedding_provider()
    await ArtifactRepository(session).set_summary(
        user_id, artifact_id, summary_text, summary_vec, provider.name, provider.dimensions,
    )
    await session.commit()


def _artifact(
    user_id: str, title: str, content: str, source: str, source_type: str,
    author: str, tags: List[str], artifact_id: Optional[str] = None, created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Content-addressed by default. Connectors pass a fixed artifact_id per source
    document, so an edited document updates its artifact (keeping review state
    and lineage of unchanged items) instead of creating a second one, and the
    document's own date as created_at so history sorts by when things happened.
    """
    return {
        "id": artifact_id or stable_id(user_id, "artifact", f"{title}:{content}"),
        "title": title,
        "content": content,
        "source": source,
        "source_type": source_type,
        "author": author,
        "tags": tags,
        "created_at": created_at or _now(),
    }


def _base_metadata(artifact: Dict[str, Any]) -> Dict[str, Any]:
    return ingestion.extract_metadata(ingestion.normalize_format({**artifact, "type": "text"}))


# ---------------------------------------------------------------------------
# Entry points (one per ingestion route)
# ---------------------------------------------------------------------------

async def ingest_text(
    session: ArcadeSession, user_id: str, title: str, content: str,
    source: str, source_type: str, author: str, tags: List[str],
    artifact_id: Optional[str] = None, created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Regex extraction — used for manually entered text."""
    artifact = _artifact(user_id, title, content, source, source_type, author, tags, artifact_id, created_at)
    items = build_regex_items(user_id, artifact["id"], content, author, tags, artifact["created_at"])
    relationships = await persist_artifact(session, user_id, artifact, items, _base_metadata(artifact))
    await index_artifact(session, user_id, artifact, items)
    return {"artifact": artifact, "items": items, "relationships": relationships, "extracted_count": len(items)}


async def ingest_document(
    session: ArcadeSession, user_id: str, title: str, content: str,
    source: str, source_type: str, author: str, tags: List[str],
    artifact_id: Optional[str] = None, created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    LLM extraction for uploaded files and fetched URLs. Regex is only a
    fallback: for the whole document when no LLM answers, or per chunk when
    the LLM reply can't be parsed. Checklists always come from regex.
    """
    artifact = _artifact(user_id, title, content, source, source_type, author, tags, artifact_id, created_at)
    created_at = artifact["created_at"]
    result = await extract_from_document(content)
    metadata = _base_metadata(artifact)

    if result.get("llm_unavailable"):
        metadata["llm_error"] = result["llm_error"]
        engine = "regex"
        items = build_regex_items(user_id, artifact["id"], content, author, tags, created_at)
    else:
        metadata["extraction"] = result["extraction"]
        if result.get("llm_error"):
            metadata["llm_error"] = result["llm_error"]
        engine = "local_llm"
        items = build_llm_items(user_id, artifact["id"], result, author, tags, created_at, _DOCUMENT_KINDS)
        for item in items:
            # chunks that fell back to regex are labelled as such in review
            if item["details"].get("extractor") == "regex":
                item["extraction_engine"] = "regex"
        items += build_checklist_items(user_id, artifact["id"], content, author, tags, created_at)
        for item in items:
            item.setdefault("extraction_engine", "regex" if item["type"] == "checklist" else engine)

    relationships = await persist_artifact(session, user_id, artifact, items, metadata, engine)
    await index_artifact(session, user_id, artifact, items)
    return {
        "artifact": artifact,
        "items": items,
        "relationships": relationships,
        "extracted_count": len(items),
        "llm_error": metadata.get("llm_error"),
    }


async def ingest_transcript(
    session: ArcadeSession, user_id: str, title: str, content: str,
    source_type: str, author: str, tags: List[str],
    artifact_id: Optional[str] = None, created_at: Optional[str] = None, source: Optional[str] = None,
) -> Dict[str, Any]:
    """LLM extraction for transcripts, emails and Slack exports."""
    llm_result = await extract_from_transcript(content, source_type=source_type)
    artifact = _artifact(user_id, title, content, source or source_type, source_type, author, tags,
                         artifact_id, created_at)
    metadata = {**_base_metadata(artifact), "summary": llm_result.get("summary", "")}
    if llm_result.get("llm_error"):
        metadata["llm_error"] = llm_result["llm_error"]
    engine = "regex" if llm_result.get("llm_error") else "local_llm"

    items = build_llm_items(user_id, artifact["id"], llm_result, author, tags, artifact["created_at"])
    relationships = await persist_artifact(session, user_id, artifact, items, metadata, engine)
    await index_artifact(session, user_id, artifact, items)
    return {
        "artifact": artifact,
        "items": items,
        "relationships": relationships,
        "extracted_count": len(items),
        "summary": llm_result.get("summary", ""),
        "llm_error": llm_result.get("llm_error"),
    }


async def ingest_structured(
    session: ArcadeSession, user_id: str, title: str, content: str,
    source: str, source_type: str, author: str, tags: List[str],
    entries: List[Dict[str, Any]], artifact_id: Optional[str] = None, created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Items the source already states explicitly (an ADR is one decision) —
    no extraction. Each entry: {prefix, type, title, details, author?,
    review_status?, declared_status?}.
    """
    artifact = _artifact(user_id, title, content, source, source_type, author, tags, artifact_id, created_at)
    items: List[Item] = []
    for entry in entries:
        item = _item(user_id, entry["prefix"], artifact["id"], entry["title"], entry["type"],
                     entry.get("author") or author, artifact["created_at"], tags, entry["details"])
        if entry.get("review_status"):
            item["review_status"] = entry["review_status"]
        items.append(item)
    relationships = await persist_artifact(session, user_id, artifact, items, _base_metadata(artifact), "structured")
    repo = KnowledgeItemRepository(session)
    declared = [(i["id"], e["declared_status"]) for i, e in zip(items, entries) if e.get("declared_status")]
    for item_id, status in declared:
        await repo.update(user_id, item_id, {"declared_status": status})
    if declared:
        await lineage.refresh_statuses(session, user_id)
        await session.commit()
    await index_artifact(session, user_id, artifact, items)
    return {"artifact": artifact, "items": items, "relationships": relationships, "extracted_count": len(items)}


async def reextract_artifact(
    session: ArcadeSession, user_id: str, artifact: Dict[str, Any], metadata: Dict[str, Any],
) -> List[Item]:
    """Re-run regex extraction after an artifact's content was edited. No summary rebuild."""
    items = build_regex_items(
        user_id, artifact["id"], artifact["content"], artifact["author"],
        artifact["tags"], artifact["created_at"],
    )
    await persist_artifact(session, user_id, artifact, items, metadata)
    await index_artifact(session, user_id, artifact, items, summarise=False)
    return items


async def import_okf(session: ArcadeSession, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Import an OKF payload as one artifact. Imported nodes become items of that
    artifact (CONTAINS edges, embedded, searchable); imported edges become LINK
    edges between them. Edges whose endpoints weren't imported are skipped.
    """
    normalized = normalize_okf_payload(payload)
    artifact = _artifact(
        user_id, normalized["title"], normalized["content"], normalized["source"],
        normalized["source_type"], normalized["author"], normalized["tags"],
    )
    metadata = {**ingestion.extract_metadata({**artifact, "type": "text"}), **normalized["metadata"]}
    items = build_regex_items(
        user_id, artifact["id"], artifact["content"], artifact["author"],
        artifact["tags"], artifact["created_at"],
    )
    new_ids: Dict[str, str] = {}   # OKF node id -> our item id
    for node in normalized["items"]:
        item_type = node["type"].replace(" ", "-").lower() or "knowledge-item"
        raw_details = {
            **node.get("details", {}),
            "okf_source": node.get("source", normalized["source"]),
            "okf_original_id": node.get("id"),
        }
        item = _item(user_id, "okf", artifact["id"], node["title"], item_type,
                     node.get("author", normalized["author"]), artifact["created_at"],
                     node.get("tags", normalized["tags"]),
                     normalize_item_details(raw_details, item_type, "okf"))
        items.append(item)
        if node.get("id"):
            new_ids[str(node["id"])] = item["id"]

    await persist_artifact(session, user_id, artifact, items, metadata)

    rel_repo = RelationshipRepository(session)
    linked = 0
    for rel in normalized["relationships"]:
        src, dst = new_ids.get(str(rel["source"])), new_ids.get(str(rel["target"]))
        if src and dst and await rel_repo.add_link(user_id, src, dst, rel["type"] or "RELATED"):
            linked += 1
    await session.commit()

    await index_artifact(session, user_id, artifact, items)
    return {
        "artifact": artifact,
        "imported_items": len(normalized["items"]),
        "relationships": linked,
        "relationships_skipped": len(normalized["relationships"]) - linked,
    }


async def reembed_all(session: ArcadeSession, user_id: str) -> Dict[str, Any]:
    """
    Re-embed all of the user's items and artifact summaries with the active
    embedding model. Run after changing LOCAL_EMBED_MODEL.
    """
    items = await KnowledgeItemRepository(session).list(user_id)
    updated_items = await embed_and_store(session, user_id, [item_dict(i) for i in items])

    provider = get_embedding_provider()
    artifacts = ArtifactRepository(session)
    updated_summaries = 0
    for row in await artifacts.summaries(user_id):
        vec = await embed(row["summary"])
        if vec:
            await artifacts.set_summary(user_id, row["id"], row["summary"], vec, provider.name, provider.dimensions)
            updated_summaries += 1
    await session.commit()
    await record_embedding_model(session.client, provider.dimensions, provider.name)
    return {
        "provider": provider.name,
        "dimensions": provider.dimensions,
        "items_reembedded": updated_items,
        "summaries_reembedded": updated_summaries,
    }


async def copy_artifact(
    session: ArcadeSession, source_space: str, target_space: str, artifact_id: str, shared_by: str,
) -> Dict[str, Any]:
    """
    Share an artifact into another space (e.g. personal -> group): the artifact,
    its items with their review state, statuses and vectors, and CONTAINS
    edges. No LLM or embedding calls. Ids are derived from the source, so
    sharing the same artifact again updates the copy instead of duplicating it.
    Lineage links aren't copied (their other ends may not exist there).
    """
    artifacts = ArtifactRepository(session)
    source = await session.query_one(
        "SELECT FROM Artifact WHERE id = :id AND user_id = :u", {"id": artifact_id, "u": source_space})
    if not source:
        raise ValueError("Artifact not found")
    new_id = stable_id(target_space, "artifact", f"shared:{source_space}:{artifact_id}")
    metadata = {**(source.get("metadata") or {}),
                "shared_from": {"space_id": source_space, "artifact_id": artifact_id,
                                "by": shared_by, "at": _now()}}
    await artifacts.upsert(
        target_space, new_id, title=source["title"], content=source["content"], source=source.get("source") or "",
        source_type=source.get("source_type") or "manual", author=source.get("author") or "unknown",
        tags=source.get("tags") or [], created_at=source.get("created_at") or _now(), metadata=metadata,
        extraction_engine=source.get("extraction_engine") or "regex",
    )
    if source.get("summary"):
        await artifacts.set_summary(target_space, new_id, source["summary"], source.get("summary_embedding"),
                                    source.get("summary_embedding_provider") or "",
                                    source.get("summary_embedding_dims") or 0)

    items_repo = KnowledgeItemRepository(session)
    rows = await session.query(
        "SELECT FROM KnowledgeItem WHERE artifact_id = :a AND user_id = :u ORDER BY @rid",
        {"a": artifact_id, "u": source_space})
    items: List[Item] = []
    for row in rows:
        item = {
            "id": stable_id(target_space, row["id"].split("_")[0], f"{new_id}:{row['id']}"),
            "artifact_id": new_id, "title": row["title"], "type": row["type"],
            "author": row.get("author") or "unknown", "date": row.get("date") or _now(),
            "tags": row.get("tags") or [], "details": row.get("details") or {},
            "review_status": row.get("review_status") or "pending",
        }
        items.append(item)
    await items_repo.sync_for_artifact(target_space, new_id, items, source.get("extraction_engine") or "regex")
    for row, item in zip(rows, items):
        await items_repo.update(target_space, item["id"], {
            "review_status": row.get("review_status") or "pending",
            "review_note": row.get("review_note") or "",
            "declared_status": row.get("declared_status"),
            "extraction_engine": row.get("extraction_engine") or "regex",
        })
        if row.get("embedding"):
            await items_repo.set_embedding(target_space, item["id"], row["embedding"],
                                           row.get("embedding_provider") or "", row.get("embedding_dims") or 0)
    await RelationshipRepository(session).ensure_contains(target_space, new_id, [i["id"] for i in items])
    await lineage.refresh_statuses(session, target_space)
    await session.commit()
    return {"artifact_id": new_id, "items": len(items)}
