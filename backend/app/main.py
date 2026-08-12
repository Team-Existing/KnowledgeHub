from __future__ import annotations

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True) or find_dotenv())

import hashlib
import os
import platform
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    TokenResponse,
    create_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.db import (
    Artifact,
    ArtifactSummary,
    CrossLink,
    KnowledgeItem,
    Playbook,
    QueryLog,
    Relationship,
    User,
    get_session,
    init_db,
)
from app.services.cross_source_linker import find_cross_links
from app.services.curation_layer import CurationLayer
from app.services.file_ingestion import extract_text_from_upload, fetch_url
from app.services.graph_builder import GraphBuilder
from app.services.graphrag import embed_items, graphrag_query, embed
from app.services.providers import OLLAMA_BASE, get_llm_provider, get_embedding_provider
from app.services.ingestion_normalization import IngestionNormalization
from app.services.knowledge_extraction import KnowledgeExtraction
from app.services.llm_extraction import extract_from_transcript
from app.services.neo4j_graph import Neo4jGraphStore
from app.services.item_schema import normalize_item_details
from app.services.okf import export_okf_payload, normalize_okf_payload

app = FastAPI(title="Knowledge Hubs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:4200", "http://127.0.0.1:4200",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ingestion = IngestionNormalization()
extractor = KnowledgeExtraction()
graph_builder = GraphBuilder(None)
curation = CurationLayer()
neo4j_graph = Neo4jGraphStore()


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    if neo4j_graph.enabled:
        neo4j_graph.ensure_vector_index()
    else:
        print("⚠️  Neo4j not configured - using SQLite fallback mode")


@app.on_event("shutdown")
def shutdown() -> None:
    neo4j_graph.close()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=6)
    

class ModelActionRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=120)
    model_config = ConfigDict(protected_namespaces=())


_RECOMMENDED_LOCAL_MODELS = {
    "llama3.1:8b": {"name": "Llama 3.1 8B", "size": "4.7 GB", "ramRequired": "8 GB+"},
    "mistral:7b": {"name": "Mistral 7B", "size": "4.1 GB", "ramRequired": "8 GB+"},
    "gpt-oss:20b": {"name": "GPT-OSS 20B", "size": "13 GB", "ramRequired": "164 GB+"},
}


async def _ollama_models() -> List[Dict[str, Any]]:
    """Return local Ollama models without making Ollama a startup requirement."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{OLLAMA_BASE}/api/tags")
            response.raise_for_status()
            return response.json().get("models", [])
    except Exception:
        return []


def _system_ram_gb() -> int:
    try:
        import psutil
        return max(1, round(psutil.virtual_memory().total / (1024 ** 3)))
    except Exception:
        # os.sysconf is available on Unix; use a safe useful fallback elsewhere.
        try:
            return max(1, round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3)))
        except Exception:
            return 8


@app.post("/auth/register", status_code=201)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, str]:
    existing = await session.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")
    user = User(
        id=str(uuid.uuid4()),
        username=body.username,
        hashed_password=hash_password(body.password),
        role="admin",
    )
    session.add(user)
    await session.commit()
    return {"user_id": user.id}


@app.post("/auth/token", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    result = await session.execute(select(User).where(User.username == form.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenResponse(access_token=create_token(user.id))


# ---------------------------------------------------------------------------
# Local model management (Ollama)
# ---------------------------------------------------------------------------

@app.get("/models/system-info")
async def model_system_info(
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    ram_gb = _system_ram_gb()
    tier = "gpt-oss:20b" if ram_gb >= 16 else "llama3.1:8b" if ram_gb >= 8 else "mistral:7b"
    return {"ramGb": ram_gb, "recommendedTier": tier, "platform": platform.system()}


@app.get("/models/local")
async def list_local_models(
    current_user: User = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    installed = {m.get("name", ""): m for m in await _ollama_models()}
    models: List[Dict[str, Any]] = []
    for model_id, details in _RECOMMENDED_LOCAL_MODELS.items():
        models.append({"id": model_id, **details, "provider": "ollama", "installed": model_id in installed})
    for model_id, raw in installed.items():
        if model_id not in _RECOMMENDED_LOCAL_MODELS:
            models.append({"id": model_id, "name": model_id, "size": "Installed", "ramRequired": "Varies", "provider": "ollama", "installed": True})
    return models


@app.get("/models/status")
async def model_status(
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    llm = get_llm_provider()
    embedding = get_embedding_provider()
    installed_names = {m.get("name", "") for m in await _ollama_models()}
    llm_model = llm.name.split(":", 1)[-1] if ":" in llm.name else llm.name
    return {
        "llm": {"provider": "local" if llm.is_local else "cloud", "model": llm_model, "installed": llm_model in installed_names},
        "embedding": {"provider": "local", "model": embedding.name.split(":", 1)[-1] if ":" in embedding.name else embedding.name, "installed": True},
    }


@app.post("/models/install")
async def install_local_model(
    body: ModelActionRequest,
    current_user: User = Depends(get_current_user),
) -> Any:
    from fastapi.responses import StreamingResponse
    import json as _json

    async def _stream():
        try:
            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=900, write=30, pool=5)) as client:
                async with client.stream("POST", f"{OLLAMA_BASE}/api/pull", json={"name": body.model_id, "stream": True}) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = _json.loads(line)
                            yield f"data: {_json.dumps(chunk)}\n\n"
                        except Exception:
                            continue
            yield f"data: {_json.dumps({'status': 'done'})}\n\n"
        except Exception as exc:
            yield f"data: {_json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/models/remove")
async def remove_local_model(
    body: ModelActionRequest,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    try:
        import httpx, json as _json
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                "DELETE",
                f"{OLLAMA_BASE}/api/delete",
                content=_json.dumps({"name": body.model_id}),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
        return {"model_id": body.model_id, "removed": True}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not remove the local model: {exc}")


@app.post("/models/set-default")
async def set_default_local_model(
    body: ModelActionRequest,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    installed = {m.get("name", "") for m in await _ollama_models()}
    if body.model_id not in installed:
        raise HTTPException(status_code=400, detail="Install this model before making it the default")
    return {"model_id": body.model_id, "default": True}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "storage": "sqlite",
        "neo4j": "connected" if neo4j_graph.verify() else "disabled",
    }


@app.get("/health/consistency")
async def consistency_check(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Compare SQLite vs Neo4j node counts to surface dual-store drift."""
    user_id = current_user.id
    sqlite_artifacts = (await session.execute(
        select(Artifact).where(Artifact.user_id == user_id)
    )).scalars().all()
    sqlite_items = (await session.execute(
        select(KnowledgeItem).where(KnowledgeItem.user_id == user_id)
    )).scalars().all()

    sqlite_artifact_ids = {a.id for a in sqlite_artifacts}
    sqlite_item_ids = {i.id for i in sqlite_items}
    sqlite_embedded = sum(1 for i in sqlite_items if i.embedding)

    result: Dict[str, Any] = {
        "sqlite": {
            "artifacts": len(sqlite_artifact_ids),
            "knowledge_items": len(sqlite_item_ids),
            "items_with_embeddings": sqlite_embedded,
        },
        "neo4j": {"status": "disabled"},
        "drift": [],
    }

    if neo4j_graph.enabled and neo4j_graph.verify():
        try:
            with neo4j_graph.driver.session(database=neo4j_graph.database) as s:
                neo4j_artifact_ids = {
                    r["id"] for r in s.run("MATCH (a:Artifact) RETURN a.id AS id")
                }
                neo4j_item_ids = {
                    r["id"] for r in s.run("MATCH (n:KnowledgeItem) RETURN n.id AS id")
                }
            result["neo4j"] = {
                "artifacts": len(neo4j_artifact_ids),
                "knowledge_items": len(neo4j_item_ids),
            }
            only_sqlite_artifacts = sqlite_artifact_ids - neo4j_artifact_ids
            only_sqlite_items = sqlite_item_ids - neo4j_item_ids
            only_neo4j_artifacts = neo4j_artifact_ids - sqlite_artifact_ids
            only_neo4j_items = neo4j_item_ids - sqlite_item_ids
            if any([only_sqlite_artifacts, only_sqlite_items, only_neo4j_artifacts, only_neo4j_items]):
                result["drift"] = [
                    {"issue": "artifacts only in SQLite", "ids": list(only_sqlite_artifacts)},
                    {"issue": "items only in SQLite", "ids": list(only_sqlite_items)},
                    {"issue": "artifacts only in Neo4j", "ids": list(only_neo4j_artifacts)},
                    {"issue": "items only in Neo4j", "ids": list(only_neo4j_items)},
                ]
                result["drift"] = [d for d in result["drift"] if d["ids"]]
        except Exception as exc:
            result["neo4j"] = {"status": "error", "detail": str(exc)}

    result["in_sync"] = len(result["drift"]) == 0
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _build_items(artifact_id: str, content: str, author: str, tags: List[str], created_at: str) -> List[Dict[str, Any]]:
    extracted = {
        "decisions": extractor.extract_decisions(content),
        "how_tos": extractor.mine_how_to_patterns(content),
        "checklists": extractor.detect_checklists(content),
        "best_practices": extractor.identify_best_practices(content),
        "lessons": extractor.extract_lessons_learned(content),
        "risks": extractor.recognize_risk_patterns(content),
    }
    items: List[Dict[str, Any]] = []
    for label in ["decisions", "how_tos", "lessons", "risks"]:
        for entry in extracted[label]:
            title = entry.get("what")  # canonical key after normalization
            if title:
                items.append({
                    "id": _stable_id(label, f"{artifact_id}:{title}"),
                    "artifact_id": artifact_id,
                    "title": title[:180],
                    "type": label.rstrip("s").replace("_", "-"),
                    "author": author,
                    "date": created_at,
                    "tags": tags,
                    "details": entry,
                })
    for practice in extracted["best_practices"]:
        items.append({
            "id": _stable_id("practice", f"{artifact_id}:{practice}"),
            "artifact_id": artifact_id,
            "title": practice[:180],
            "type": "best-practice",
            "author": author,
            "date": created_at,
            "tags": tags,
            "details": {"practice": practice},
        })
    for checklist in extracted["checklists"]:
        items.append({
            "id": _stable_id("checklist", f"{artifact_id}:{checklist}"),
            "artifact_id": artifact_id,
            "title": checklist[:180],
            "type": "checklist",
            "author": author,
            "date": created_at,
            "tags": tags,
            "details": {"item": checklist},
        })
    return items


async def _persist_artifact(
    session: AsyncSession,
    user_id: str,
    artifact_id: str,
    title: str,
    content: str,
    source: str,
    source_type: str,
    author: str,
    tags: List[str],
    created_at: str,
    metadata: Dict[str, Any],
    extraction_engine: str = "regex",
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    existing = await session.get(Artifact, artifact_id)
    if existing:
        existing.title = title
        existing.content = content
        existing.tags = tags
        existing.metadata_ = metadata
        existing.extraction_engine = extraction_engine
    else:
        session.add(Artifact(
            id=artifact_id,
            user_id=user_id,
            title=title,
            content=content,
            source=source,
            source_type=source_type,
            author=author,
            tags=tags,
            extraction_engine=extraction_engine,
            created_at=created_at,
            metadata_=metadata,
        ))

    items = _build_items(artifact_id, content, author, tags, created_at)
    item_ids = {i["id"] for i in items}

    # remove stale items for this artifact that are no longer extracted
    existing_items_result = await session.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.artifact_id == artifact_id,
            KnowledgeItem.user_id == user_id,
        )
    )
    for old in existing_items_result.scalars():
        if old.id not in item_ids:
            await session.delete(old)

    for item in items:
        ki = await session.get(KnowledgeItem, item["id"])
        if ki:
            ki.title = item["title"]
            ki.details = item["details"]
            ki.extraction_engine = extraction_engine
        else:
            session.add(KnowledgeItem(
                id=item["id"],
                user_id=user_id,
                artifact_id=artifact_id,
                title=item["title"],
                type=item["type"],
                author=item["author"],
                date=item["date"],
                tags=item["tags"],
                details=item["details"],
                extraction_engine=extraction_engine,
                review_status="pending",
            ))

    relationships = [
        {"from": artifact_id, "to": item["id"], "type": "CONTAINS"}
        for item in items
    ]
    existing_rels = await session.execute(
        select(Relationship).where(Relationship.from_id == artifact_id, Relationship.user_id == user_id)
    )
    existing_rel_keys = {(r.from_id, r.to_id, r.type) for r in existing_rels.scalars()}
    for rel in relationships:
        key = (rel["from"], rel["to"], rel["type"])
        if key not in existing_rel_keys:
            session.add(Relationship(
                id=str(uuid.uuid4()),
                user_id=user_id,
                from_id=rel["from"],
                to_id=rel["to"],
                type=rel["type"],
            ))

    await session.commit()
    return items, relationships


# ---------------------------------------------------------------------------
# Knowledge – read
# ---------------------------------------------------------------------------

@app.get("/knowledge")
async def list_knowledge(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user_id = current_user.id
    artifacts = (await session.execute(select(Artifact).where(Artifact.user_id == user_id))).scalars().all()
    items = (await session.execute(select(KnowledgeItem).where(KnowledgeItem.user_id == user_id))).scalars().all()
    rels = (await session.execute(select(Relationship).where(Relationship.user_id == user_id))).scalars().all()
    playbooks = (await session.execute(select(Playbook).where(Playbook.user_id == user_id))).scalars().all()
    
    return {
        "artifacts": [_artifact_dict(a) for a in artifacts],
        "knowledge_items": [_item_dict(i) for i in items],
        "relationships": [{"from": r.from_id, "to": r.to_id, "type": r.type} for r in rels],
        "playbooks": [{"id": p.id, "title": p.title, "steps": p.steps, "category": p.category} for p in playbooks],
    }


@app.post("/knowledge/okf/import")
async def import_okf_payload(
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    normalized = normalize_okf_payload(payload)
    artifact_id = _stable_id("artifact", f"{normalized['title']}:{normalized['content']}")
    created_at = datetime.utcnow().isoformat()

    artifact_dict = {
        "id": artifact_id,
        "title": normalized["title"],
        "content": normalized["content"],
        "source": normalized["source"],
        "source_type": normalized["source_type"],
        "author": normalized["author"],
        "tags": normalized["tags"],
        "created_at": created_at,
    }
    metadata = {**ingestion.extract_metadata({**artifact_dict, "type": "text"}), **normalized["metadata"]}

    items, relationships = await _persist_artifact(
        session=session,
        user_id=current_user.id,
        artifact_id=artifact_id,
        title=normalized["title"],
        content=normalized["content"],
        source=normalized["source"],
        source_type=normalized["source_type"],
        author=normalized["author"],
        tags=normalized["tags"],
        created_at=created_at,
        metadata=metadata,
    )

    for item in normalized["items"]:
        item_id = _stable_id("okf", f"{artifact_id}:{item['title']}")
        item_type = item["type"].replace(" ", "-").lower() or "knowledge-item"
        raw_details = {**item.get("details", {}), "okf_source": item.get("source", normalized["source"]), "okf_original_id": item.get("id")}
        item_payload = {
            "id": item_id,
            "artifact_id": artifact_id,
            "title": item["title"][:180],
            "type": item_type,
            "author": item.get("author", normalized["author"]),
            "date": created_at,
            "tags": item.get("tags", normalized["tags"]),
            "details": normalize_item_details(raw_details, item_type, "okf"),
            "user_id": current_user.id,
            "review_status": "pending",
        }
        existing = await session.get(KnowledgeItem, item_payload["id"])
        if existing:
            existing.title = item_payload["title"]
            existing.details = item_payload["details"]
            existing.tags = item_payload["tags"]
        else:
            session.add(KnowledgeItem(
                id=item_payload["id"],
                user_id=current_user.id,
                artifact_id=artifact_id,
                title=item_payload["title"],
                type=item_payload["type"],
                author=item_payload["author"],
                date=item_payload["date"],
                tags=item_payload["tags"],
                details=item_payload["details"],
                review_status=item_payload["review_status"],
            ))

    for rel in normalized["relationships"]:
        session.add(Relationship(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            from_id=rel["source"],
            to_id=rel["target"],
            type=rel["type"],
        ))

    await session.commit()
    neo4j_graph.upsert_artifact_graph(artifact_dict, items, relationships)
    await _embed_and_store(items, session)
    await _build_artifact_summary(session, current_user.id, artifact_dict, normalized["content"])
    return {"artifact": artifact_dict, "imported_items": len(normalized["items"]), "relationships": len(normalized["relationships"])}


@app.get("/knowledge/okf/export")
async def export_okf(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user_id = current_user.id
    artifacts = (await session.execute(select(Artifact).where(Artifact.user_id == user_id))).scalars().all()
    items = (await session.execute(select(KnowledgeItem).where(KnowledgeItem.user_id == user_id))).scalars().all()
    rels = (await session.execute(select(Relationship).where(Relationship.user_id == user_id))).scalars().all()
    artifact_payloads = [_artifact_dict(a) for a in artifacts]
    item_payloads = [_item_dict(i) for i in items]
    relationship_payloads = [{"from": r.from_id, "to": r.to_id, "type": r.type} for r in rels]
    return export_okf_payload(user_id, artifact_payloads, item_payloads, relationship_payloads)


# ---------------------------------------------------------------------------
# Knowledge – ingest (text)
# ---------------------------------------------------------------------------

class ArtifactRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    source: str = "manual"
    author: str = "unknown"
    tags: List[str] = []


@app.post("/knowledge/artifacts")
async def ingest_artifact(
    request: ArtifactRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    return await _ingest(
        session=session,
        user_id=current_user.id,
        title=request.title,
        content=request.content,
        source=request.source,
        source_type="manual",
        author=request.author,
        tags=request.tags,
    )


# ---------------------------------------------------------------------------
# Knowledge – ingest (file upload)
# ---------------------------------------------------------------------------

@app.post("/knowledge/artifacts/upload")
async def ingest_file(
    file: UploadFile = File(...),
    title: str = Form(...),
    author: str = Form("unknown"),
    tags: str = Form(""),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    content = await extract_text_from_upload(file)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    return await _ingest(
        session=session,
        user_id=current_user.id,
        title=title,
        content=content,
        source="file",
        source_type="file",
        author=author,
        tags=tag_list,
    )


# ---------------------------------------------------------------------------
# Knowledge – ingest (URL)
# ---------------------------------------------------------------------------

class UrlIngestRequest(BaseModel):
    url: str
    title: str = Field(min_length=1, max_length=200)
    author: str = "unknown"
    tags: List[str] = []


@app.post("/knowledge/artifacts/url")
async def ingest_url(
    request: UrlIngestRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    content = await fetch_url(request.url)
    return await _ingest(
        session=session,
        user_id=current_user.id,
        title=request.title,
        content=content,
        source=request.url,
        source_type="url",
        author=request.author,
        tags=request.tags,
    )


async def _ingest(
    session: AsyncSession,
    user_id: str,
    title: str,
    content: str,
    source: str,
    source_type: str,
    author: str,
    tags: List[str],
) -> Dict[str, Any]:
    artifact_id = _stable_id("artifact", f"{title}:{content}")
    created_at = datetime.utcnow().isoformat()
    artifact_dict = {"id": artifact_id, "title": title, "content": content, "source": source, "source_type": source_type, "author": author, "tags": tags, "created_at": created_at}
    normalized = ingestion.normalize_format({**artifact_dict, "type": "text"})
    metadata = ingestion.extract_metadata(normalized)
    items, relationships = await _persist_artifact(session, user_id, artifact_id, title, content, source, source_type, author, tags, created_at, metadata)
    neo4j_graph.upsert_artifact_graph(artifact_dict, items, relationships)
    await _embed_and_store(items, session)
    await _build_artifact_summary(session, user_id, artifact_dict, content)
    return {"artifact": artifact_dict, "items": items, "relationships": relationships, "extracted_count": len(items)}


async def _embed_and_store(
    items: List[Dict[str, Any]], session: AsyncSession
) -> None:
    """
    Embed extracted items and persist vectors to both Neo4j (primary) and
    SQLite (backup). Provenance columns (embedding_provider, embedding_dims)
    are written so a provider switch can be detected and a re-embed triggered.
    """
    pairs = await embed_items(items)
    if not pairs:
        return
    
    provider_name = get_embedding_provider().name
    provider_dims = get_embedding_provider().dimensions
    for item_id, vector in pairs:
        ki = await session.get(KnowledgeItem, item_id)
        if ki:
            ki.embedding = vector
            ki.embedding_provider = provider_name
            ki.embedding_dims = provider_dims

        neo4j_graph.upsert_item_embedding(item_id, vector)

    await session.commit()


async def _build_artifact_summary(
    session: AsyncSession,
    user_id: str,
    artifact: Dict[str, Any],
    content: str,
) -> None:
    """Generate and persist a condensed LLM summary for the artifact (summary index)."""
    from app.services.llm_extraction import _summarise_text
    existing = (await session.execute(
        select(ArtifactSummary).where(ArtifactSummary.artifact_id == artifact["id"])
    )).scalar_one_or_none()

    summary_text = await _summarise_text(content)
    if not summary_text:
        return

    summary_vec = await embed(summary_text)
    created_at  = datetime.utcnow().isoformat()

    provider_name = get_embedding_provider().name
    provider_dims = get_embedding_provider().dimensions
    if existing:
        existing.summary   = summary_text
        existing.embedding = summary_vec
        existing.embedding_provider = provider_name
        existing.embedding_dims = provider_dims
    else:
        session.add(ArtifactSummary(
            user_id=user_id,
            artifact_id=artifact["id"],
            summary=summary_text,
            embedding=summary_vec,
            embedding_provider=provider_name,
            embedding_dims=provider_dims,
            created_at=created_at,
        ))
    await session.commit()

    # push to Neo4j summary index
    if summary_vec:
        neo4j_graph.upsert_artifact_summary(artifact["id"], summary_text, summary_vec)


# ---------------------------------------------------------------------------
# Review workflow
# ---------------------------------------------------------------------------

@app.get("/knowledge/review")
async def list_review_queue(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> List[Dict[str, Any]]:
    result = await session.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.user_id == current_user.id,
            KnowledgeItem.review_status == "pending",
        )
    )
    return [_item_dict(i) for i in result.scalars()]


class ReviewDecision(BaseModel):
    status: str = Field(pattern="^(accepted|rejected)$")
    note: str = ""
    title: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


@app.patch("/knowledge/review/{item_id}")
async def review_item(
    item_id: str,
    body: ReviewDecision,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    item = await session.get(KnowledgeItem, item_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Item not found")
    item.review_status = body.status
    item.review_note = body.note
    if body.title:
        item.title = body.title
    if body.details is not None:
        item.details = body.details
    await session.commit()
    return _item_dict(item)


# ---------------------------------------------------------------------------
# Transcript ingestion (LLM)
# ---------------------------------------------------------------------------

class TranscriptRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    source_type: str = "transcript"  # transcript | email | slack
    author: str = "unknown"
    tags: List[str] = []


@app.post("/knowledge/artifacts/transcript")
async def ingest_transcript(
    request: TranscriptRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    llm_result = await extract_from_transcript(request.content, source_type=request.source_type)

    artifact_id = _stable_id("artifact", f"{request.title}:{request.content}")
    created_at = datetime.utcnow().isoformat()
    artifact_dict = {
        "id": artifact_id, "title": request.title, "content": request.content,
        "source": request.source_type, "source_type": request.source_type,
        "author": request.author, "tags": request.tags, "created_at": created_at,
    }
    normalized = ingestion.normalize_format({**artifact_dict, "type": "text"})
    metadata = {**ingestion.extract_metadata(normalized), "summary": llm_result.get("summary", "")}
    if llm_result.get("llm_error"):
        metadata["llm_error"] = llm_result["llm_error"]

    # Build items from LLM output — details are already normalized by _normalize_llm_result
    items: List[Dict[str, Any]] = []
    for d in llm_result.get("decisions", []):
        t = d.get("what", "").strip()[:180]
        if t:
            items.append({"id": _stable_id("decision", f"{artifact_id}:{t}"), "artifact_id": artifact_id,
                          "title": t, "type": "decision", "author": d.get("who") or request.author,
                          "date": created_at, "tags": request.tags, "details": d})
    for a in llm_result.get("action_items", []):
        t = a.get("what", "").strip()[:180]  # normalized: task → what
        if t:
            items.append({"id": _stable_id("action", f"{artifact_id}:{t}"), "artifact_id": artifact_id,
                          "title": t, "type": "action-item", "author": a.get("who") or request.author,
                          "date": created_at, "tags": request.tags, "details": a})
    for r in llm_result.get("risks", []):
        t = r.get("what", "").strip()[:180]  # normalized: risk → what
        if t:
            items.append({"id": _stable_id("risk", f"{artifact_id}:{t}"), "artifact_id": artifact_id,
                          "title": t, "type": "risk", "author": request.author,
                          "date": created_at, "tags": request.tags, "details": r})

    # Persist
    existing = await session.get(Artifact, artifact_id)
    if not existing:
        session.add(Artifact(
            id=artifact_id, user_id=current_user.id,
            title=request.title, content=request.content,
            source=request.source_type, source_type=request.source_type,
            author=request.author, tags=request.tags,
            extraction_engine="local_llm" if not llm_result.get("llm_error") else "regex",
            created_at=created_at, metadata_=metadata,
        ))
    else:
        existing.metadata_ = metadata

    item_ids = {i["id"] for i in items}
    old_items = (await session.execute(
        select(KnowledgeItem).where(KnowledgeItem.artifact_id == artifact_id)
    )).scalars().all()
    for old in old_items:
        if old.id not in item_ids:
            await session.delete(old)

    for item in items:
        ki = await session.get(KnowledgeItem, item["id"])
        if ki:
            ki.title = item["title"]; ki.details = item["details"]
            ki.extraction_engine = "local_llm" if not llm_result.get("llm_error") else "regex"
        else:
            session.add(KnowledgeItem(
                id=item["id"], user_id=current_user.id,
                artifact_id=artifact_id, title=item["title"], type=item["type"],
                author=item["author"], date=item["date"], tags=item["tags"],
                details=item["details"], extraction_engine="local_llm" if not llm_result.get("llm_error") else "regex", review_status="pending",
            ))

    relationships = [{"from": artifact_id, "to": i["id"], "type": "CONTAINS"} for i in items]
    existing_rel_keys = {
        (r.from_id, r.to_id, r.type)
        for r in (await session.execute(
            select(Relationship).where(Relationship.from_id == artifact_id)
        )).scalars()
    }
    for rel in relationships:
        if (rel["from"], rel["to"], rel["type"]) not in existing_rel_keys:
            session.add(Relationship(
                id=str(uuid.uuid4()), user_id=current_user.id,
                from_id=rel["from"], to_id=rel["to"], type=rel["type"],
            ))

    await session.commit()
    neo4j_graph.upsert_artifact_graph(artifact_dict, items, relationships)
    await _embed_and_store(items, session)
    await _build_artifact_summary(session, current_user.id, artifact_dict, request.content)

    return {
        "artifact": artifact_dict,
        "items": items,
        "relationships": relationships,
        "extracted_count": len(items),
        "summary": llm_result.get("summary", ""),
        "llm_error": llm_result.get("llm_error"),
    }


# ---------------------------------------------------------------------------
# GraphRAG query
# ---------------------------------------------------------------------------

class GraphRagRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=20)
    history: List[Dict[str, str]] = []   # [{"role": "user"|"assistant", "content": "..."}]


@app.post("/knowledge/graphrag/query")
async def graphrag_query_endpoint(
    request: GraphRagRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user_id = current_user.id
    t0 = datetime.utcnow()

    all_items = (
        await session.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.user_id == user_id,
                KnowledgeItem.review_status != "rejected",
            )
        )
    ).scalars().all()

    fallback = [{**_item_dict(i), "embedding": i.embedding} for i in all_items]

    cross_links_rows = (
        await session.execute(select(CrossLink).where(CrossLink.user_id == user_id))
    ).scalars().all()
    cross_links = [{"item_id_a": cl.item_id_a, "item_id_b": cl.item_id_b} for cl in cross_links_rows]

    # load artifact summaries for the summary index fallback
    summary_rows = (
        await session.execute(select(ArtifactSummary).where(ArtifactSummary.user_id == user_id))
    ).scalars().all()
    artifact_summaries = [
        {"artifact_id": s.artifact_id, "title": "", "summary": s.summary, "embedding": s.embedding}
        for s in summary_rows
    ]

    result = await graphrag_query(
        question=request.question,
        neo4j_store=neo4j_graph,
        fallback_items=fallback,
        cross_links=cross_links,
        artifact_summaries=artifact_summaries,
        history=request.history or None,
        top_k=request.top_k,
    )

    # persist query log
    latency_ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
    session.add(QueryLog(
        user_id=current_user.id,
        question=request.question,
        sub_queries=result.get("sub_queries", []),
        hyde_doc=result.get("hyde_doc"),
        route=result.get("route"),
        retrieval_mode=result.get("retrieval_mode"),
        llm_provider=get_llm_provider().name,
        embedding_provider=get_embedding_provider().name,
        context_node_ids=[n.get("id") for n in result.get("context_nodes", [])],
        citations=result.get("citations", []),
        answer_snippet=result.get("answer", "")[:400],
        latency_ms=result.get("latency_ms", latency_ms),
        created_at=t0.isoformat(),
    ))
    await session.commit()

    return result


# ---------------------------------------------------------------------------
# Cross-source linking
# ---------------------------------------------------------------------------

@app.post("/knowledge/link")
async def run_cross_link(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user_id = current_user.id
    all_items = (await session.execute(
        select(KnowledgeItem).where(KnowledgeItem.user_id == user_id)
    )).scalars().all()

    item_dicts = [{"id": i.id, "artifact_id": i.artifact_id, "title": i.title} for i in all_items]
    links = find_cross_links(item_dicts)

    old_links = (await session.execute(
        select(CrossLink).where(CrossLink.user_id == user_id)
    )).scalars().all()
    for old in old_links:
        await session.delete(old)

    created = []
    for id_a, id_b, score in links:
        cl = CrossLink(
            id=str(uuid.uuid4()),
            user_id=user_id,
            item_id_a=id_a,
            item_id_b=id_b,
            score=str(score),
        )
        session.add(cl)
        created.append({"item_id_a": id_a, "item_id_b": id_b, "score": score})

        # Also persist as graph relationships
        session.add(Relationship(
            id=str(uuid.uuid4()), user_id=user_id,
            from_id=id_a, to_id=id_b, type="RELATED_TO",
        ))

    await session.commit()
    return {"links_created": len(created), "links": created}


@app.get("/knowledge/links")
async def get_cross_links(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> List[Dict[str, Any]]:
    result = await session.execute(
        select(CrossLink).where(CrossLink.user_id == current_user.id)
    )
    return [{"item_id_a": cl.item_id_a, "item_id_b": cl.item_id_b, "score": float(cl.score)}
            for cl in result.scalars()]


# ---------------------------------------------------------------------------
# Re-embedding job (Phase 2) — triggered after provider switch
# ---------------------------------------------------------------------------

@app.post("/knowledge/reembed")
async def reembed_workspace(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    Re-embed all knowledge items and artifact summaries using the currently
    active embedding provider. Run this after switching EMBEDDING_PROVIDER
    to avoid mixing incompatible vector spaces.
    """
    user_id = current_user.id
    items = (
        await session.execute(select(KnowledgeItem).where(KnowledgeItem.user_id == user_id))
    ).scalars().all()

    item_dicts = [_item_dict(i) for i in items]
    pairs = await embed_items(item_dicts)
    updated_items = 0
    provider_name = get_embedding_provider().name
    provider_dims = get_embedding_provider().dimensions
    for item_id, vector in pairs:
        ki = await session.get(KnowledgeItem, item_id)
        if ki:
            ki.embedding = vector
            ki.embedding_provider = provider_name
            ki.embedding_dims = provider_dims
            neo4j_graph.upsert_item_embedding(item_id, vector)
            updated_items += 1

    # Re-embed artifact summaries
    summaries = (
        await session.execute(select(ArtifactSummary).where(ArtifactSummary.user_id == user_id))
    ).scalars().all()
    updated_summaries = 0
    for s in summaries:
        vec = await embed(s.summary)
        if vec:
            s.embedding = vec
            s.embedding_provider = provider_name
            s.embedding_dims = provider_dims
            neo4j_graph.upsert_artifact_summary(s.artifact_id, s.summary, vec)
            updated_summaries += 1

    await session.commit()
    return {
        "provider": provider_name,
        "dimensions": provider_dims,
        "items_reembedded": updated_items,
        "summaries_reembedded": updated_summaries,
    }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.get("/knowledge/search")
async def search_knowledge(
    q: str = "",
    type: Optional[str] = None,
    source_type: Optional[str] = None,
    tag: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user_id = current_user.id
    q_lower = q.strip().lower()

    items_q = select(KnowledgeItem).where(KnowledgeItem.user_id == user_id)
    if type:
        items_q = items_q.where(KnowledgeItem.type == type)
    all_items = (await session.execute(items_q)).scalars().all()

    artifacts_q = select(Artifact).where(Artifact.user_id == user_id)
    if source_type:
        artifacts_q = artifacts_q.where(Artifact.source_type == source_type)
    all_artifacts = (await session.execute(artifacts_q)).scalars().all()
    artifact_map = {a.id: a for a in all_artifacts}

    def _matches_item(i: KnowledgeItem) -> bool:
        if source_type and artifact_map.get(i.artifact_id or "") is None:
            return False
        if tag and tag.lower() not in [t.lower() for t in (i.tags or [])]:
            return False
        if not q_lower:
            return True
        haystack = f"{i.title} {' '.join(i.tags or [])} {i.type}".lower()
        return q_lower in haystack

    def _matches_artifact(a: Artifact) -> bool:
        if tag and tag.lower() not in [t.lower() for t in (a.tags or [])]:
            return False
        if not q_lower:
            return True
        haystack = f"{a.title} {a.author} {' '.join(a.tags or [])}".lower()
        return q_lower in haystack

    matched_items = [_item_dict(i) for i in all_items if _matches_item(i)]
    matched_artifacts = [_artifact_dict(a) for a in all_artifacts if _matches_artifact(a)]

    return {
        "query": q,
        "filters": {"type": type, "source_type": source_type, "tag": tag},
        "knowledge_items": matched_items,
        "artifacts": matched_artifacts,
        "total": len(matched_items) + len(matched_artifacts),
    }


# ---------------------------------------------------------------------------
# Playbooks
# ---------------------------------------------------------------------------

class PlaybookRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    steps: List[Dict[str, Any]]


@app.post("/knowledge/playbooks")
async def create_playbook(
    request: PlaybookRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    playbook = curation.build_playbook(request.title, request.steps)
    session.add(Playbook(
        id=playbook["id"],
        user_id=current_user.id,
        title=playbook["title"],
        steps=playbook["steps"],
        category=playbook["category"],
    ))
    await session.commit()
    return playbook


# ---------------------------------------------------------------------------
# Artifacts – update / delete
# ---------------------------------------------------------------------------

class ArtifactUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    content: Optional[str] = Field(default=None, min_length=1, max_length=100_000)
    tags: Optional[List[str]] = None


@app.put("/knowledge/artifacts/{artifact_id}")
async def update_artifact(
    artifact_id: str,
    body: ArtifactUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    artifact = await session.get(Artifact, artifact_id)
    if not artifact or artifact.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if body.title is not None:
        artifact.title = body.title
    if body.tags is not None:
        artifact.tags = body.tags
    if body.content is not None:
        artifact.content = body.content
        # re-extract items when content changes
        items, relationships = await _persist_artifact(
            session, current_user.id, artifact_id,
            artifact.title, body.content, artifact.source,
            artifact.source_type or "manual", artifact.author,
            artifact.tags or [], artifact.created_at, artifact.metadata_ or {},
        )
        neo4j_graph.upsert_artifact_graph(_artifact_dict(artifact), items, relationships)
        await _embed_and_store(items, session)
        return {**_artifact_dict(artifact), "items": items}
    await session.commit()
    return _artifact_dict(artifact)


@app.delete("/knowledge/artifacts/{artifact_id}")
async def delete_artifact(
    artifact_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    artifact = await session.get(Artifact, artifact_id)
    if not artifact or artifact.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    for model, col in [
        (KnowledgeItem, KnowledgeItem.artifact_id),
        (Relationship, Relationship.from_id),
        (ArtifactSummary, ArtifactSummary.artifact_id),
    ]:
        rows = (await session.execute(select(model).where(col == artifact_id))).scalars().all()
        for row in rows:
            await session.delete(row)
    await session.delete(artifact)
    await session.commit()
    # Mirror deletion in Neo4j.  A graph failure must not silently leave
    # SQLite cleaned but the graph intact — log it and let /health/consistency
    # surface any residual drift, but at least stop generating new drift.
    neo4j_error: str | None = None
    if neo4j_graph.enabled:
        try:
            neo4j_graph.delete_artifact_graph(artifact_id)
        except Exception as exc:
            neo4j_error = str(exc)
            logger.error(
                "Neo4j delete_artifact_graph(%s) failed after SQLite commit: %s",
                artifact_id, exc,
            )
    if neo4j_error:
        # 207 signals partial success: SQLite is clean, graph may have drift.
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=207,
            content={"detail": "Artifact deleted from SQLite but Neo4j cleanup failed", "neo4j_error": neo4j_error},
        )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Knowledge items – update / delete
# ---------------------------------------------------------------------------

class ItemUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    tags: Optional[List[str]] = None
    details: Optional[Dict[str, Any]] = None


@app.get("/knowledge/items/{item_id}")
async def get_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    item = await session.get(KnowledgeItem, item_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Item not found")
    return _item_dict(item)


@app.put("/knowledge/items/{item_id}")
async def update_item(
    item_id: str,
    body: ItemUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    item = await session.get(KnowledgeItem, item_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Item not found")
    if body.title is not None:
        item.title = body.title
    if body.tags is not None:
        item.tags = body.tags
    if body.details is not None:
        item.details = body.details
    await session.commit()
    return _item_dict(item)


@app.delete("/knowledge/items/{item_id}")
async def delete_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    item = await session.get(KnowledgeItem, item_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Item not found")
    rels = (await session.execute(
        select(Relationship).where(
            (Relationship.from_id == item_id) | (Relationship.to_id == item_id),
            Relationship.user_id == current_user.id,
        )
    )).scalars().all()
    for rel in rels:
        await session.delete(rel)
    await session.delete(item)
    await session.commit()
    neo4j_error: str | None = None
    if neo4j_graph.enabled:
        try:
            neo4j_graph.delete_item(item_id)
        except Exception as exc:
            neo4j_error = str(exc)
            logger.error(
                "Neo4j delete_item(%s) failed after SQLite commit: %s",
                item_id, exc,
            )
    if neo4j_error:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=207,
            content={"detail": "Item deleted from SQLite but Neo4j cleanup failed", "neo4j_error": neo4j_error},
        )
    return Response(status_code=204)

# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

@app.get("/knowledge/graph")
async def knowledge_graph(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    if neo4j_graph.enabled and neo4j_graph.verify():
        neo4j_data = neo4j_graph.visualization_data()
        if neo4j_data["nodes"]:
            return neo4j_data

    user_id = current_user.id
    artifacts = (await session.execute(select(Artifact).where(Artifact.user_id == user_id))).scalars().all()
    items = (await session.execute(select(KnowledgeItem).where(KnowledgeItem.user_id == user_id))).scalars().all()
    rels = (await session.execute(select(Relationship).where(Relationship.user_id == user_id))).scalars().all()

    nodes = [{"id": a.id, "label": a.title, "type": "artifact"} for a in artifacts] + \
            [{"id": i.id, "label": i.title, "type": i.type} for i in items]
    edges = [{"from": r.from_id, "to": r.to_id, "type": r.type} for r in rels]
    return graph_builder.prepare_visualization_data({"nodes": nodes, "edges": edges})


@app.get("/knowledge/{item_id}")
async def get_knowledge_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Get a knowledge item by ID with fallback."""
    item = await session.get(KnowledgeItem, item_id)
    if item and item.user_id == current_user.id:
        return _item_dict(item)

    items = await session.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.user_id == current_user.id,
            KnowledgeItem.id.like(f"%{item_id.split('_')[-1]}")
        )
    )
    item = items.scalar_one_or_none()
    if item:
        return _item_dict(item)

    items = await session.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.user_id == current_user.id,
            KnowledgeItem.title.ilike(f"%{item_id}%")
        )
    )
    item = items.scalar_one_or_none()
    if item:
        return _item_dict(item)

    raise HTTPException(status_code=404, detail="Item not found")


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _artifact_dict(a: Artifact) -> Dict[str, Any]:
    return {
        "id": a.id,
        "title": a.title,
        "content": a.content,
        "source": a.source,
        "source_type": a.source_type or "manual",
        "author": a.author,
        "tags": a.tags or [],
        "extraction_engine": a.extraction_engine or "regex",
        "created_at": a.created_at,
        "metadata": a.metadata_ or {},
    }


def _item_dict(i: KnowledgeItem) -> Dict[str, Any]:
    return {
        "id": i.id,
        "artifact_id": i.artifact_id,
        "title": i.title,
        "type": i.type,
        "author": i.author,
        "date": i.date,
        "tags": i.tags or [],
        "details": i.details or {},
        "extraction_engine": i.extraction_engine or "regex",
        "review_status": i.review_status,
        "review_note": i.review_note or "",
    }
