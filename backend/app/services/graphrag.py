"""
GraphRAG pipeline — local-first.

Stages
------
1. Query transformation   – rewrite into sub-queries (+ HyDE when LLM available)
2. Query routing          – classify intent → pick retrieval strategy
3. Fusion retrieval       – vector ANN + BM25 merged with RRF
4. Summary index          – inject artifact-level summaries as context prefix
5. Graph expansion        – walk CONTAINS / RELATED_TO neighbours
6. Reranking              – LLM cross-encoder rerank of top candidates
7. Context assembly       – structured window: summaries | ranked items | neighbours
8. LLM generation         – grounded answer with multi-turn history support

Embedding and LLM calls are routed through embeddings.py / llm_client.py,
which default to sentence-transformers + Ollama.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.services import embeddings as emb
from app.services import llm_client

if TYPE_CHECKING:
    from app.repositories.graph import GraphStore

logger = logging.getLogger(__name__)

# ── route → system prompt ────────────────────────────────────────────────────
_SYSTEM: Dict[str, str] = {
    "factual": (
        "You are a precise knowledge assistant. "
        "Answer using ONLY the provided context nodes — do not infer, extrapolate, or stitch together implications. "
        "Every factual claim MUST be directly stated in a context node and cited as [item_id]. "
        "If the context does not explicitly contain the answer, say so. "
        "Implying an answer from weak or tangential context is a worse failure than declining to answer."
    ),
    "exploratory": (
        "You are a knowledge assistant helping with open-ended exploration. "
        "Synthesise ONLY what is explicitly stated across the context nodes — do not infer connections that are not directly supported. "
        "Cite every claim as [item_id]. "
        "Acknowledge gaps honestly. "
        "Implying an answer from weak or tangential context is a worse failure than declining to answer."
    ),
    "comparative": (
        "You are a knowledge assistant specialised in comparison. "
        "Compare ONLY what is explicitly stated in the context nodes — do not infer attributes that are not directly present. "
        "Cite each point as [item_id]. "
        "If the context does not support a direct comparison, say so rather than implying one."
    ),
    "procedural": (
        "You are a knowledge assistant specialised in step-by-step guidance. "
        "Present steps using ONLY content explicitly found in the context nodes. "
        "Cite each step as [item_id]. "
        "Do not fill gaps with assumed or inferred steps — if a step is missing from the context, say so."
    ),
}
_SYSTEM["fallback"] = _SYSTEM["factual"]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Embedding helpers (delegated to embeddings.py)
# ─────────────────────────────────────────────────────────────────────────────

async def embed(text: str) -> Optional[List[float]]:
    return await emb.embed(text)


async def embed_items(items: List[Dict[str, Any]]) -> List[Tuple[str, List[float]]]:
    if not items:
        return []
    texts = [_item_text(i) for i in items]
    vecs = await emb.embed_batch(texts)
    return [(items[i]["id"], v) for i, v in enumerate(vecs) if v is not None]


def _item_text(item: Dict[str, Any]) -> str:
    parts = [item.get("title", ""), item.get("type", "")]
    for v in (item.get("details") or {}).values():
        if isinstance(v, str) and v:
            parts.append(v)
    return " ".join(parts)[:2000]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Query transformation  (sub-queries + HyDE)
# ─────────────────────────────────────────────────────────────────────────────

async def transform_query(question: str) -> Dict[str, Any]:
    prompt = (
        "Rewrite the user question below into 3 distinct versions that cover "
        "different angles (synonyms, specificity levels, related concepts). "
        "Also write a short hypothetical answer document (2-3 sentences) that "
        "a perfect knowledge base would contain.\n\n"
        "OUTPUT RULES:\n"
        "- Return ONLY a JSON object, no markdown, no explanation.\n"
        "- Shape: {\"sub_queries\": [\"...\", \"...\", \"...\"], \"hyde_doc\": \"...\"}\n\n"
        f"Question: {question}"
    )
    content = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=300,
        json_mode=True,
    )
    if content:
        try:
            # tolerate accidental markdown fences from local models
            import re as _re
            cleaned = _re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=_re.IGNORECASE)
            cleaned = _re.sub(r"\s*```$", "", cleaned.strip())
            data = json.loads(cleaned)
            sub_queries = data.get("sub_queries") or [question]
            hyde_doc    = data.get("hyde_doc") or question
            if question not in sub_queries:
                sub_queries = [question] + sub_queries[:3]
            # Phase 3: cap fan-out at 4 to keep local latency acceptable
            return {"sub_queries": sub_queries[:4], "hyde_doc": hyde_doc}
        except Exception as exc:
            logger.warning("transform_query() parse failed: %s", exc)
    return {"sub_queries": [question], "hyde_doc": question}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Query routing
# ─────────────────────────────────────────────────────────────────────────────

_ROUTE_HINTS = {
    "factual":     ["what is", "who is", "when did", "define", "which"],
    "comparative": ["compare", "difference", "versus", "vs", "better", "pros and cons"],
    "procedural":  ["how to", "steps", "process", "guide", "implement", "set up"],
    "exploratory": ["why", "explain", "tell me about", "overview", "discuss"],
}

# Canonical route exemplars for embedding-similarity classification (Phase 3).
# Avoids a full LLM generation call for unambiguous queries.
_ROUTE_EXEMPLARS: Dict[str, List[str]] = {
    "factual":     ["what is X", "who decided Y", "when was Z approved", "which option was chosen"],
    "comparative": ["compare A and B", "difference between X and Y", "pros and cons of Z"],
    "procedural":  ["how to deploy", "steps to set up", "guide for implementing", "process for onboarding"],
    "exploratory": ["why did we choose", "explain the architecture", "tell me about the risks", "overview of the project"],
}

_exemplar_vecs: Optional[Dict[str, List[List[float]]]] = None


async def _get_exemplar_vecs() -> Dict[str, List[List[float]]]:
    """Lazily embed route exemplars once and cache them."""
    global _exemplar_vecs
    if _exemplar_vecs is not None:
        return _exemplar_vecs
    phrases = [(route, p) for route, ps in _ROUTE_EXEMPLARS.items() for p in ps]
    vecs = await emb.embed_batch([p for _, p in phrases])
    result: Dict[str, List[List[float]]] = {route: [] for route in _ROUTE_EXEMPLARS}
    for (route, _), vec in zip(phrases, vecs):
        if vec is not None:
            result[route].append(vec)
    _exemplar_vecs = result
    return result


def _cosine_simple(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


async def _route_by_embedding(q_vec: Optional[List[float]]) -> Optional[str]:
    """
    Classify query intent using embedding similarity against route exemplars.
    Returns a route label when confidence is clear (best score > 0.55 and
    margin over second-best > 0.05), otherwise returns None so the caller
    can fall back to LLM classification.
    """
    if q_vec is None:
        return None
    exemplars = await _get_exemplar_vecs()
    route_scores: Dict[str, float] = {}
    for route, vecs in exemplars.items():
        if vecs:
            route_scores[route] = max(_cosine_simple(q_vec, v) for v in vecs)
    if not route_scores:
        return None
    ranked = sorted(route_scores.items(), key=lambda x: x[1], reverse=True)
    best_route, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score > 0.55 and (best_score - second_score) > 0.05:
        return best_route
    return None


def route_query(question: str) -> str:
    q = question.lower()
    for route, hints in _ROUTE_HINTS.items():
        if any(h in q for h in hints):
            return route
    return "exploratory"


async def route_query_llm(question: str, q_vec: Optional[List[float]] = None) -> str:
    """q_vec: the question's embedding, if the caller already has it."""
    # Phase 3: try embedding-similarity classifier first to save an LLM call
    if q_vec is None:
        q_vec = await emb.embed(question)
    embedding_route = await _route_by_embedding(q_vec)
    if embedding_route:
        return embedding_route

    # Ambiguous — fall back to LLM classification
    prompt = (
        "Classify the question into exactly one category.\n"
        "Categories: factual | exploratory | comparative | procedural\n"
        "Rules:\n"
        "- factual: asks for a specific fact, decision, or definition\n"
        "- exploratory: open-ended, asks for explanation or overview\n"
        "- comparative: compares two or more things\n"
        "- procedural: asks for steps or a how-to guide\n"
        "Return ONLY the single category word, nothing else.\n\n"
        f"Question: {question}"
    )
    content = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=10,
    )
    if content:
        label = content.strip().lower().split()[0] if content.strip() else ""
        if label in _SYSTEM:
            return label
    return route_query(question)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Reciprocal Rank Fusion
# ─────────────────────────────────────────────────────────────────────────────

def _rrf(ranked_lists: List[List[Dict[str, Any]]], k: int = 60) -> List[Dict[str, Any]]:
    """
    Fuse ranked lists with RRF. `rrf_score` is normalised to [0, 1] by the
    best achievable score (rank 1 in every list), so thresholds mean the same
    thing whether one list or several were fused.
    """
    scores: Dict[str, float] = defaultdict(float)
    items_by_id: Dict[str, Dict[str, Any]] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            iid = item.get("id", "")
            if not iid:
                continue
            scores[iid] += 1.0 / (k + rank)
            items_by_id[iid] = item
    merged = sorted(items_by_id.values(), key=lambda i: scores[i["id"]], reverse=True)
    max_score = len(ranked_lists) / (k + 1)
    for item in merged:
        item["rrf_score"] = scores[item["id"]] / max_score if max_score else 0.0
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# 8. LLM reranking
# ─────────────────────────────────────────────────────────────────────────────

# GRAPHRAG_RERANK=off skips the LLM rerank entirely (RRF order is used).
RERANK_ENABLED = os.getenv("GRAPHRAG_RERANK", "on").lower() not in ("0", "off", "false", "no")
# Only worth an LLM call when there is a real choice to make: with at most
# top_n * RERANK_MIN_RATIO candidates, the RRF order is kept as-is.
RERANK_MIN_RATIO = 2


def should_rerank(n_candidates: int, top_n: int) -> bool:
    return RERANK_ENABLED and n_candidates > top_n * RERANK_MIN_RATIO


async def rerank(
    question: str,
    candidates: List[Dict[str, Any]],
    top_n: int = 8,
) -> List[Dict[str, Any]]:
    if not should_rerank(len(candidates), top_n):
        return candidates[:top_n]

    snippets = [
        f"{i}: [{c.get('kind') or c.get('type', '')}] {c.get('title') or c.get('label', '')}"
        for i, c in enumerate(candidates)
    ]
    prompt = (
        f"Question: {question}\n\n"
        "Rate each candidate's relevance to the question from 0 (irrelevant) "
        "to 10 (highly relevant).\n\n"
        "OUTPUT RULES:\n"
        "- Return ONLY a JSON array of integers, one per candidate, in the same order.\n"
        "- Example for 4 candidates: [8, 3, 10, 1]\n"
        "- No markdown, no explanation, no extra keys.\n\n"
        "Candidates:\n" + "\n".join(snippets)
    )
    content = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=256,
        json_mode=True,
    )
    if content:
        try:
            import re as _re
            cleaned = _re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=_re.IGNORECASE)
            cleaned = _re.sub(r"\s*```$", "", cleaned.strip())
            raw = json.loads(cleaned)
            scores: List[int] = raw if isinstance(raw, list) else raw.get("scores", [])
            if len(scores) == len(candidates):
                paired = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
                return [c for _, c in paired[:top_n]]
        except Exception as exc:
            logger.warning("rerank() parse failed: %s — using RRF order", exc)
    return candidates[:top_n]


# ─────────────────────────────────────────────────────────────────────────────
# 9. Context assembly
# ─────────────────────────────────────────────────────────────────────────────

def _build_context(
    summaries: List[Dict[str, Any]],
    ranked_items: List[Dict[str, Any]],
    graph_neighbours: List[Dict[str, Any]],
) -> str:
    parts: List[str] = []
    if summaries:
        lines = ["=== ARTIFACT SUMMARIES ==="]
        for s in summaries:
            lines.append(f"[{s.get('artifact_id','?')}] {s.get('title','')}: {s.get('summary','')}")
        parts.append("\n".join(lines))
    if ranked_items:
        lines = ["=== KNOWLEDGE ITEMS ==="]
        for n in ranked_items:
            nid   = n.get("id", "?")
            title = n.get("title") or n.get("label", "")
            kind  = n.get("kind") or n.get("type", "")
            score = n.get("rrf_score") or n.get("score") or 0.0
            status = f" status={n['status']}" if n.get("status") else ""
            lines.append(f"[{nid}] ({kind}{status}) relevance={score:.3f}: {title}")
            for succ in n.get("replaced_by") or []:
                lines.append(f"  replaced_by: [{succ['id']}] ({succ['kind']}) {succ.get('title') or ''}")
            for k, v in (n.get("details") or {}).items():
                if isinstance(v, str) and v and k not in ("evidence", "confidence"):
                    lines.append(f"  {k}: {v}")
        parts.append("\n".join(lines))
    if graph_neighbours:
        lines = ["=== RELATED CONTEXT (graph) ==="]
        for n in graph_neighbours:
            status = f" status={n['status']}" if n.get("status") else ""
            lines.append(f"[{n.get('id','?')}] ({n.get('kind') or n.get('type','')}{status}): {n.get('title') or n.get('label','')}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# 10. LLM generation
# ─────────────────────────────────────────────────────────────────────────────

async def _generate(
    question: str,
    context: str,
    route: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    system = _SYSTEM.get(route, _SYSTEM["factual"])
    system += """
    
When citing sources, copy the exact bracketed id shown before each context entry, e.g. [decision_1a2b3c4d5e6f].
Never cite by title and never shorten or invent ids.

Entries marked status=superseded or status=reversed are history, not current policy.
Never present them as the current decision: give the decision that replaced them
(named in "replaced_by") as current, and mention the older one only as background.

Format your answer as a well-structured response:
- Use bullet points for lists
- Group related information together
- Provide clear, actionable advice
- End with a brief summary
"""
    user_msg = f"Context:\n{context}\n\nQuestion: {question}"
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": user_msg})

    content = await llm_client.chat(
        messages=messages,
        temperature=0.2,
        max_tokens=1000,
    )
    if content:
        return content
    
    # ✅ Improved fallback - group by type and format nicely
    return _format_fallback_answer(question, context)


def _format_fallback_answer(question: str, context: str) -> str:
    """Format a nice fallback answer when LLM is unavailable."""
    lines = context.splitlines()
    
    best_practices = []
    how_tos = []
    lessons = []
    decisions = []
    risks = []
    
    current_section = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line == "=== KNOWLEDGE ITEMS ===":
            current_section = "items"
            continue
        if line == "=== ARTIFACT SUMMARIES ===":
            current_section = "summaries"
            continue
        if line == "=== RELATED CONTEXT (graph) ===":
            current_section = "graph"
            continue
        if line.startswith("==="):
            current_section = None
            continue
        
        # Parse item lines
        if current_section == "items" and "(" in line and ")" in line:
            # Extract type and content
            parts = line.split(":", 1)
            if len(parts) == 2:
                meta = parts[0].strip()
                content_text = parts[1].strip()

                # Extract type (and lifecycle status, if any) from "[id] (type status=x) relevance=…"
                m = re.match(r"\[[^\]]*\]\s*\(([\w-]+)(?:\s+status=(\w+))?\)", meta)
                kind, status = (m.group(1), m.group(2)) if m else ("", None)
                if status in ("superseded", "reversed"):
                    content_text = f"{content_text} _({status})_"
                if kind == "best-practice":
                    best_practices.append(content_text)
                elif kind == "how-to":
                    how_tos.append(content_text)
                elif kind == "lesson":
                    lessons.append(content_text)
                elif kind == "decision":
                    decisions.append(content_text)
                elif kind == "risk":
                    risks.append(content_text)
    
    # Build formatted answer
    answer_parts = []
    answer_parts.append(f"Based on the provided context, here are insights about **{question}**:\n")
    
    if how_tos:
        answer_parts.append("### 📋 How-To Steps")
        for i, item in enumerate(how_tos[:5], 1):
            answer_parts.append(f"{i}. {item}")
        answer_parts.append("")
    
    if best_practices:
        answer_parts.append("### ✅ Best Practices")
        for item in best_practices[:5]:
            answer_parts.append(f"• {item}")
        answer_parts.append("")
    
    if lessons:
        answer_parts.append("### 📚 Lessons Learned")
        for item in lessons[:5]:
            answer_parts.append(f"• {item}")
        answer_parts.append("")
    
    if decisions:
        answer_parts.append("### 📝 Key Decisions")
        for item in decisions[:5]:
            answer_parts.append(f"• {item}")
        answer_parts.append("")
    
    if risks:
        answer_parts.append("### ⚠️ Risks to Consider")
        for item in risks[:5]:
            answer_parts.append(f"• {item}")
        answer_parts.append("")
    
    if not any([how_tos, best_practices, lessons, decisions, risks]):
        return f"No specific guidance found for: *{question}*. Please try rephrasing your question."
    
    return "\n".join(answer_parts)


# ─────────────────────────────────────────────────────────────────────────────
# 11. Citation extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_citations(
    answer: str,
    nodes: List[Dict[str, Any]],
    summaries: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """
    Citation format is the full id in brackets — `[item_id]` for knowledge
    items, `[artifact_id]` for artifact summaries — exactly as written by
    _build_context. Only exact matches against the supplied context count.
    """
    by_id: Dict[str, Dict[str, str]] = {}
    for n in nodes:
        nid = n.get("id")
        if nid:
            by_id[nid] = {
                "id": nid,
                "title": n.get("title") or n.get("label") or nid,
                "type": n.get("kind") or n.get("type") or "item",
            }
    for s in summaries or []:
        aid = s.get("artifact_id")
        if aid and aid not in by_id:
            by_id[aid] = {"id": aid, "title": s.get("title") or aid, "type": "artifact"}

    cited: List[Dict[str, str]] = []
    seen_ids = set()
    for token in re.findall(r"\[([^\]]+)\]", answer):
        # tolerate "[id1, id2]" groupings
        for part in token.split(","):
            cid = part.strip()
            if cid in by_id and cid not in seen_ids:
                seen_ids.add(cid)
                cited.append(by_id[cid])
    return cited


# ─────────────────────────────────────────────────────────────────────────────
# 12. Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def graphrag_query(
    question: str,
    store: GraphStore,
    user_id: str,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: int = 8,
) -> Dict[str, Any]:
    t0 = time.monotonic()
    timings: Dict[str, int] = {}
    mark = [t0]

    def lap(stage: str) -> None:
        now = time.monotonic()
        timings[stage] = int((now - mark[0]) * 1000)
        mark[0] = now

    async def embed_and_route() -> Tuple[Optional[List[float]], str]:
        q_vec = await emb.embed(question)
        return q_vec, await route_query_llm(question, q_vec)

    # Query transformation (LLM) and routing depend only on the question, so
    # they run concurrently; routing reuses the question embedding.
    transformed, (q_vec, route) = await asyncio.gather(transform_query(question), embed_and_route())
    sub_queries  = transformed["sub_queries"]
    hyde_doc     = transformed["hyde_doc"]
    lap("transform_route")

    # Every other text that needs an embedding goes in one batch.
    hyde_texts = [hyde_doc] if hyde_doc != question else []
    sub_texts = [sq for sq in sub_queries[1:3] if sq != question]
    batch = hyde_texts + sub_texts
    batch_vecs = await emb.embed_batch(batch) if batch else []
    hyde_vec = batch_vecs[0] if hyde_texts and batch_vecs else None
    sub_vecs = batch_vecs[len(hyde_texts):]
    query_vecs = [v for v in (q_vec, hyde_vec) if v is not None]
    lap("embed")

    # Ranked lists to fuse: vector seeds + their graph neighbourhood for the
    # question and HyDE vectors, plain vector hits for sub-queries, and a
    # full-text keyword list for exact-term recall.
    ranked_lists: List[List[Dict[str, Any]]] = []
    for qv in query_vecs:
        hits = await store.retrieve_for_rag(user_id, qv, top_k=top_k)
        if hits:
            ranked_lists.append(hits)
    for sqv in sub_vecs:
        if sqv:
            hits = await store.vector_search(user_id, sqv, top_k=max(top_k // 2, 1))
            if hits:
                ranked_lists.append([{**h, "retrieved_by": "vector"} for h in hits])
    keyword_hits = await store.keyword_search(user_id, question, top_k=top_k)
    if keyword_hits:
        ranked_lists.append([{**h, "retrieved_by": "keyword"} for h in keyword_hits])

    all_context_nodes: List[Dict[str, Any]] = _rrf(ranked_lists) if ranked_lists else []
    retrieval_mode = "graph_vector_keyword" if all_context_nodes else "none"
    summaries_used = await store.summary_search(user_id, query_vecs[0], top_k=3) if query_vecs else []

    lap("retrieve")
    if not all_context_nodes:
        return {
            "answer": "No relevant knowledge found for this question.",
            "citations": [],
            "context_nodes": [],
            "summaries": [],
            "route": route,
            "sub_queries": sub_queries,
            "retrieval_mode": "none",
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "timings_ms": timings,
        }

    reranked  = await rerank(question, all_context_nodes, top_n=top_k)
    lap("rerank" if should_rerank(len(all_context_nodes), top_k) else "rerank_skipped")

    # rrf_score is normalised to [0, 1] (see _rrf): 1.0 = rank 1 in every list.
    # Drop nodes with negligible scores to avoid surfacing unrelated content.
    _MIN_RRF = 0.1
    reranked = [n for n in reranked if (n.get("rrf_score") or 0.0) >= _MIN_RRF] or reranked[:3]

    # Threshold gate — if the best node doesn't clear the confidence floor,
    # the context is too weak to ground a reliable answer. Fail loudly rather
    # than letting the LLM stitch together an implied response from noise.
    # The LLM rerank may reorder nodes, so take the max rather than reranked[0].
    _CONFIDENCE_FLOOR = 0.5
    top_score = max((n.get("rrf_score") or 0.0 for n in reranked), default=0.0)
    if top_score < _CONFIDENCE_FLOOR:
        return {
            "answer": "The retrieved context does not contain sufficiently relevant information to answer this question reliably. Please add more relevant artifacts or rephrase your query.",
            "citations": [],
            "context_nodes": reranked,
            "summaries": summaries_used,
            "route": route,
            "sub_queries": sub_queries,
            "hyde_doc": hyde_doc,
            "retrieval_mode": retrieval_mode,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "timings_ms": timings,
        }

    # point stale decisions at whatever replaced them, so the answer uses the current one
    stale = [n["id"] for n in reranked if n.get("status") in ("superseded", "reversed")]
    if stale:
        successors = await store.successors(user_id, stale)
        for n in reranked:
            if n["id"] in successors:
                n["replaced_by"] = successors[n["id"]]

    primary   = [n for n in reranked if "graph" not in n.get("retrieved_by", "")]
    graph_nbr = [n for n in reranked if "graph" in n.get("retrieved_by", "")]

    context   = _build_context(summaries_used, primary, graph_nbr)
    answer    = await _generate(question, context, route, history)
    lap("generate")
    citations = _extract_citations(answer, reranked, summaries_used)

    return {
        "answer": answer,
        "citations": citations,
        "context_nodes": reranked,
        "summaries": summaries_used,
        "route": route,
        "sub_queries": sub_queries,
        "hyde_doc": hyde_doc,
        "retrieval_mode": retrieval_mode,
        "latency_ms": int((time.monotonic() - t0) * 1000),
        "timings_ms": timings,
    }
