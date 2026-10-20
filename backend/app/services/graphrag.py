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

# ── the answer prompt ───────────────────────────────────────────────────────
# Written for small local models (llama3.1:8b class). Measured on evals/: the
# earlier prompts ("implying an answer is a worse failure than declining")
# made the model deny facts that were in its context, and asking it to copy
# long ids made it cite by title instead; numbered entries fix both.
NO_ANSWER = "The knowledge base doesn't cover this."

_ANSWER_SYSTEM = f"""\
You answer questions about a team's knowledge base: its decisions, risks, action items, lessons,
how-tos and best practices. Use only the numbered context entries.

- State what the entries say, including reasons, owners, dates and what replaced what. Leave out
  details the entries don't give: never write "not specified".
- After each statement, cite the entries it comes from by number, like [2] or [1][3].
- If an entry answers the question even in part, answer with what it says.
- Only if no entry is about the question at all, reply with exactly: {NO_ANSWER}
- An entry marked superseded or reversed is history: give the one that replaced it as current.
- Be brief. Answer first, in at most 120 words. Use a short bullet list only for several separate
  points. No preamble and no closing summary.\
"""

_ROUTE_HINT: Dict[str, str] = {
    "factual": "Answer the specific question directly.",
    "exploratory": "Cover the main points the entries make.",
    "comparative": "Compare the options point by point.",
    "procedural": "If the question asks how to do something, give the steps in order.",
}
# kept for route classification and older callers
_SYSTEM: Dict[str, str] = {route: _ANSWER_SYSTEM + "\n" + hint for route, hint in _ROUTE_HINT.items()}
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

# GRAPHRAG_QUERY_REWRITE=on rewrites the question into sub-queries plus a hypothetical
# answer (HyDE) with the LLM before retrieving. Off by default: on evals/ it cost a full
# LLM call (about 25 s on a CPU) and retrieval found the right source without it.
QUERY_REWRITE = os.getenv("GRAPHRAG_QUERY_REWRITE", "off").lower() in ("1", "on", "true", "yes")


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


# GRAPHRAG_LLM_ROUTE=on asks the LLM to classify questions the embedding classifier is
# unsure about. Off by default: the route only picks a one-line hint in the answer prompt,
# not worth an LLM call (about 5 s on a CPU); the keyword rules decide instead.
LLM_ROUTE = os.getenv("GRAPHRAG_LLM_ROUTE", "off").lower() in ("1", "on", "true", "yes")


async def route_query_llm(question: str, q_vec: Optional[List[float]] = None) -> str:
    """q_vec: the question's embedding, if the caller already has it."""
    # Phase 3: try embedding-similarity classifier first to save an LLM call
    if q_vec is None:
        q_vec = await emb.embed(question)
    embedding_route = await _route_by_embedding(q_vec)
    if embedding_route:
        return embedding_route
    if not LLM_ROUTE:
        return route_query(question)

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

# GRAPHRAG_RERANK=on adds an LLM rerank of the fused candidates. Off by default: on
# evals/ it cost about 27 s per question on a CPU, and the fusion (RRF) order already
# put the right source in the context every time.
RERANK_ENABLED = os.getenv("GRAPHRAG_RERANK", "off").lower() in ("1", "on", "true", "yes")
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

_DETAIL_SKIP = {"extractor", "confidence", "evidence", "what", "okf_original_id"}


def _detail_line(details: Dict[str, Any]) -> str:
    parts = []
    for key, value in (details or {}).items():
        if key in _DETAIL_SKIP or value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            value = "; ".join(str(v) for v in value[:8])
        parts.append(f"{key}: {str(value)[:200]}")
    return " | ".join(parts)


def _build_context(
    summaries: List[Dict[str, Any]],
    ranked_items: List[Dict[str, Any]],
    graph_neighbours: List[Dict[str, Any]],
) -> Tuple[str, Dict[int, Dict[str, Any]]]:
    """
    The numbered context the model answers from, and what each number refers to.
    Items carry their details (who, why, due, steps…) and the passage of the source
    they came from: without them the model saw only titles, and answered "the context
    doesn't say who" for an action item whose owner was on record.
    """
    refs: Dict[int, Dict[str, Any]] = {}
    parts: List[str] = []

    def add(entry: Dict[str, Any]) -> int:
        refs[len(refs) + 1] = entry
        return len(refs)

    # number every item first, so "replaced by" can point at a later entry
    numbers = {id(n): add(n) for n in ranked_items}
    number_of = {n.get("id"): numbers[id(n)] for n in ranked_items}
    if ranked_items:
        lines = ["=== KNOWLEDGE ITEMS ==="]
        for n in ranked_items:
            num = numbers[id(n)]
            kind = n.get("kind") or n.get("type", "")
            status = f", {n['status']}" if n.get("status") else ""
            lines.append(f"[{num}] {kind}{status}: {n.get('title') or n.get('label', '')}")
            details = _detail_line(n.get("details") or {})
            if details:
                lines.append(f"    {details}")
            for succ in n.get("replaced_by") or []:
                ref = f"[{number_of[succ['id']]}] " if succ.get("id") in number_of else ""
                lines.append(f"    replaced by: {ref}{succ.get('title') or succ['id']}")
            if n.get("passage"):
                lines.append(f'    from "{n.get("source_title") or "a source"}": "{n["passage"]}"')
        parts.append("\n".join(lines))
    if graph_neighbours:
        lines = ["=== RELATED ITEMS ==="]
        for n in graph_neighbours:
            num = add(n)
            status = f", {n['status']}" if n.get("status") else ""
            lines.append(f"[{num}] {n.get('kind') or n.get('type', '')}{status}: {n.get('title') or n.get('label', '')}")
        parts.append("\n".join(lines))
    if summaries:
        lines = ["=== SOURCE SUMMARIES ==="]
        for sm in summaries:
            num = add({"id": sm.get("artifact_id"), "title": sm.get("title"), "kind": "artifact"})
            lines.append(f'[{num}] summary of "{sm.get("title", "")}": {sm.get("summary", "")}')
        parts.append("\n".join(lines))
    return "\n\n".join(parts), refs


def _sentences(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]


def _passage(content: str, about: str, limit: int = 360) -> str:
    """The stretch of a source that best matches `about`: its best sentence and the next ones."""
    sentences = _sentences(content or "")
    words = _significant_words(about)
    if not sentences or not words:
        return ""
    scores = [len(_significant_words(sn) & words) for sn in sentences]
    best = max(range(len(sentences)), key=lambda i: scores[i])
    if scores[best] == 0:
        return ""
    passage, i = sentences[best], best + 1
    while i < len(sentences) and len(passage) + len(sentences[i]) < limit:
        passage += " " + sentences[i]
        i += 1
    return passage[:limit]


async def _hydrate(store: "GraphStore", user_id: str, nodes: List[Dict[str, Any]]) -> None:
    """Add details, the source title and a source passage to each retrieved item, in place."""
    from app.repositories import ArtifactRepository, KnowledgeItemRepository
    ids = [n["id"] for n in nodes if n.get("id") and n.get("kind") != "artifact"]
    full = {r["id"]: r for r in await KnowledgeItemRepository(store.session).get_many(user_id, ids)}
    artifacts: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        item = full.get(n.get("id"))
        if not item:
            continue
        n["details"] = item.get("details") or {}
        aid = item.get("artifact_id")
        if aid and aid not in artifacts:
            artifacts[aid] = await ArtifactRepository(store.session).get_owned(user_id, aid) or {}
        art = artifacts.get(aid) or {}
        n["source_title"] = art.get("title")
        about = " ".join([item.get("title") or ""] + [str(v) for v in n["details"].values() if isinstance(v, str)])
        n["passage"] = _passage(art.get("content") or "", about)


# ─────────────────────────────────────────────────────────────────────────────
# 10. LLM generation
# ─────────────────────────────────────────────────────────────────────────────

async def _generate(
    question: str,
    context: str,
    route: str,
    history: Optional[List[Dict[str, str]]] = None,
    nodes: Optional[List[Dict[str, Any]]] = None,
) -> str:
    system = _SYSTEM.get(route, _SYSTEM["factual"])
    user_msg = f"Context:\n{context}\n\nQuestion: {question}"
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": user_msg})

    content = await llm_client.chat(
        messages=messages,
        temperature=0.1,
        max_tokens=450,
    )
    if content:
        return content
    return _format_fallback_answer(question, nodes or [])


def _format_fallback_answer(question: str, nodes: List[Dict[str, Any]]) -> str:
    """An answer without the LLM: the retrieved items, grouped by type."""
    groups = [("how-to", "How-to steps"), ("best-practice", "Best practices"), ("lesson", "Lessons learned"),
              ("decision", "Decisions"), ("risk", "Risks"), ("action-item", "Action items")]
    parts = [f"The local model didn't answer, so here is what the knowledge base holds on **{question}**:\n"]
    for kind, heading in groups:
        entries = [n for n in nodes if (n.get("kind") or n.get("type")) == kind][:5]
        if entries:
            parts.append(f"### {heading}")
            for n in entries:
                status = f" _({n['status']})_" if n.get("status") in ("superseded", "reversed") else ""
                parts.append(f"- {n.get('title') or n.get('label', '')}{status}")
            parts.append("")
    if len(parts) == 1:
        return f"No specific guidance found for: *{question}*. Please try rephrasing your question."
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# 11. Citation extraction
# ─────────────────────────────────────────────────────────────────────────────

def _citable(
    nodes: List[Dict[str, Any]], summaries: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, str]]:
    """Everything the answer could cite, by id: the context items and artifact summaries."""
    by_id: Dict[str, Dict[str, str]] = {}
    for n in nodes:
        nid = n.get("id")
        if nid:
            by_id[nid] = {
                "id": nid,
                "title": n.get("title") or n.get("label") or nid,
                "type": n.get("kind") or n.get("type") or "item",
            }
            if n.get("status"):
                by_id[nid]["status"] = n["status"]   # e.g. "superseded": shown next to the source
    for s in summaries or []:
        aid = s.get("artifact_id")
        if aid and aid not in by_id:
            by_id[aid] = {"id": aid, "title": s.get("title") or aid, "type": "artifact"}
    return by_id


def _extract_citations(
    answer: str,
    nodes: List[Dict[str, Any]],
    summaries: Optional[List[Dict[str, Any]]] = None,
    refs: Optional[Dict[int, Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """
    Citations are the entry numbers of the context, "[2]" or "[1][3]" or "[1, 3]"
    (see _build_context), mapped back through `refs`; a full id in brackets is
    accepted too. Only entries of the supplied context count.
    """
    by_id = _citable(nodes, summaries)
    for ref in (refs or {}).values():
        if ref.get("id") and ref["id"] not in by_id:
            by_id[ref["id"]] = {"id": ref["id"], "title": ref.get("title") or ref["id"],
                                "type": ref.get("kind") or ref.get("type") or "item"}
    numbered = {str(num): ref.get("id") for num, ref in (refs or {}).items()}
    cited: List[Dict[str, str]] = []
    seen_ids = set()
    for token in re.findall(r"\[([^\]]+)\]", answer):
        # tolerate "[id1, id2]" groupings
        for part in token.split(","):
            cid = numbered.get(part.strip(), part.strip())
            if cid in by_id and cid not in seen_ids:
                seen_ids.add(cid)
                cited.append(by_id[cid])
    return cited


_CITE_STOPWORDS = frozenset(
    "the and for with that this from into our your their was were are has have had not but use used "
    "using will would can could should may might its it's about which what when where who how all any "
    "also than then them they there these those been being does did done per via".split()
)
_INFERRED_MAX = 4


def _significant_words(text: str) -> set:
    # "ADR-0001" stays one word: it names a record
    return {w for w in re.findall(r"[a-z0-9]+(?:-[0-9]+)?", text.lower())
            if len(w) > 2 and w not in _CITE_STOPWORDS}


_NO_ANSWER_MARKERS = ("doesn't cover", "does not cover", "no information", "not mention", "does not contain",
                      "doesn't contain", "unable to answer", "cannot answer", "no relevant", "not provide")


def is_no_answer(answer: str) -> bool:
    """A reply saying the knowledge base has nothing on the question (short, with a refusal phrase)."""
    a = answer.strip().lower()
    return a.startswith(NO_ANSWER.lower().rstrip(".")) or (
        len(a) < 400 and any(m in a for m in _NO_ANSWER_MARKERS))


def _infer_citations(
    answer: str,
    nodes: List[Dict[str, Any]],
    summaries: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """
    Sources for an answer that cites none. Small local models often name a
    source ("(ADR-0001)") instead of writing its [id], which left a grounded
    answer with no sources at all. A context entry counts when the answer names
    an identifier from its title (a word with a digit, such as ADR-0003), or
    when at least half of its title's words, and at least two, appear in the
    answer. Marked `inferred`, so the UI can say these were matched, not cited.
    """
    if is_no_answer(answer):
        return []   # a refusal names the topics it didn't find; they aren't its sources
    answer_words = _significant_words(answer)
    scored = []
    for entry in _citable(nodes, summaries).values():
        title_words = _significant_words(entry["title"])
        shared = title_words & answer_words
        names_it = any(any(ch.isdigit() for ch in w) for w in shared)
        if title_words and (names_it or len(shared) >= max(2, math.ceil(len(title_words) / 2))):
            scored.append((len(shared) / len(title_words), {**entry, "inferred": True}))
    scored.sort(key=lambda pair: -pair[0])
    return [entry for _, entry in scored[:_INFERRED_MAX]]


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
    if QUERY_REWRITE:
        transformed, (q_vec, route) = await asyncio.gather(transform_query(question), embed_and_route())
    else:
        transformed = {"sub_queries": [question], "hyde_doc": question}
        q_vec, route = await embed_and_route()
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
    await _hydrate(store, user_id, primary)
    lap("hydrate")

    context, refs = _build_context(summaries_used, primary, graph_nbr)
    answer    = await _generate(question, context, route, history, nodes=reranked)
    lap("generate")
    if is_no_answer(answer):
        citations = []
    else:
        citations = (_extract_citations(answer, reranked, summaries_used, refs)
                     or _infer_citations(answer, reranked, summaries_used))

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
