"""GraphRAG question answering and vector-index maintenance."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app.auth import get_current_user
from app.db import User, get_session
from app.repositories import GraphStore, QueryLogRepository
from app.services import artifact_pipeline as pipeline
from app.services.graphrag import graphrag_query
from app.services.providers import get_embedding_provider, get_llm_provider

router = APIRouter(prefix="/knowledge", tags=["graphrag"])


class GraphRagRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=20)
    history: List[Dict[str, str]] = []   # [{"role": "user"|"assistant", "content": "..."}]


@router.post("/graphrag/query")
async def graphrag_query_endpoint(
    request: GraphRagRequest,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    user_id = current_user.id
    t0 = datetime.now(timezone.utc)
    result = await graphrag_query(
        question=request.question,
        store=GraphStore(session),
        user_id=user_id,
        history=request.history or None,
        top_k=request.top_k,
    )

    latency_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    await QueryLogRepository(session).add(
        user_id=user_id,
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
    )
    await session.commit()
    return result


@router.post("/reembed")
async def reembed_workspace(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Re-embed everything with the active model — run after changing LOCAL_EMBED_MODEL."""
    return await pipeline.reembed_all(session, current_user.id)
