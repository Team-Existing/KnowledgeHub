"""
Full-text search over knowledge items and artifacts with ArcadeDB FULL_TEXT
(Lucene) indexes, kept up to date by ArcadeDB on every write.

  KnowledgeItem[title,tags_text,type]              item search
  Artifact[title,author,tags_text,content]         artifact search
  Artifact[title,author,tags_text]                 ranking tier (see below)

Every word of the query must match as a prefix. Artifact content is
searchable but ranked in a second tier: artifacts whose title/author/tags
match come first, body-only matches after — Lucene scores alone would let a
long document that repeats a word outrank a title hit. Body-only matches come
back with a snippet showing the hit.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.arcadedb import ArcadeSession
from app.repositories.artifacts import _FIELDS as ARTIFACT_FIELDS
from app.repositories.artifacts import ArtifactRepository
from app.repositories.knowledge_items import _FIELDS as ITEM_FIELDS

Row = Dict[str, Any]

ITEM_INDEX = "KnowledgeItem[title,tags_text,type]"
ARTIFACT_INDEX = "Artifact[title,author,tags_text,content]"
ARTIFACT_PRIMARY_INDEX = "Artifact[title,author,tags_text]"


def query_terms(q: str) -> List[str]:
    return [t.lower() for t in re.findall(r"\w+", q)]


def lucene_query(q: str, require_all: bool = True) -> str:
    """User text → Lucene query of prefix terms. Only \\w characters survive, so no operator injection."""
    prefix = "+" if require_all else ""
    return " ".join(f"{prefix}{t}*" for t in query_terms(q))


def snippet(content: str, terms: Sequence[str], radius: int = 60) -> Optional[str]:
    """A window of content around the first word that starts with one of the terms."""
    if not content or not terms:
        return None
    match = re.search(r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\w*", content, flags=re.IGNORECASE)
    if not match:
        return None
    start, end = max(0, match.start() - radius), min(len(content), match.end() + radius)
    window = " ".join(content[start:end].split())
    return ("…" if start else "") + window + ("…" if end < len(content) else "")


class SearchRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def search_items(
        self,
        user_id: str,
        q: str = "",
        *,
        type: Optional[str] = None,
        source_type: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 100,
    ) -> Tuple[List[Row], int]:
        where = ["user_id = :u"]
        params: Row = {"u": user_id, "limit": limit}
        if type:
            where.append("type = :type")
            params["type"] = type
        if tag:
            where.append("tags_lc CONTAINS :tag")
            params["tag"] = tag.lower()
        if source_type:
            # items whose own artifact has this source type
            where.append("artifact_id IN :aids")
            params["aids"] = await ArtifactRepository(self.session).ids_with_source_type(user_id, source_type) or [""]
        order = "@rid"
        if q.strip():
            lucene = lucene_query(q)
            if not lucene:
                return [], 0
            where.append(f"SEARCH_INDEX('{ITEM_INDEX}', :q) = true")
            params["q"] = lucene
            order = "$score DESC"
        where_sql = " AND ".join(where)
        total = await self.session.scalar(f"SELECT count(*) AS n FROM KnowledgeItem WHERE {where_sql}", params, 0)
        rows = await self.session.query(
            f"SELECT {ITEM_FIELDS} FROM KnowledgeItem WHERE {where_sql} ORDER BY {order} LIMIT :limit", params
        )
        return rows, total

    async def search_artifacts(
        self,
        user_id: str,
        q: str = "",
        *,
        source_type: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 100,
    ) -> Tuple[List[Row], int, Dict[str, str]]:
        """Also returns {artifact_id: snippet} for artifacts matched in their content."""
        where = ["user_id = :u"]
        params: Row = {"u": user_id, "limit": limit}
        if source_type:
            where.append("source_type = :st")
            params["st"] = source_type
        if tag:
            where.append("tags_lc CONTAINS :tag")
            params["tag"] = tag.lower()

        if not q.strip():
            where_sql = " AND ".join(where)
            total = await self.session.scalar(f"SELECT count(*) AS n FROM Artifact WHERE {where_sql}", params, 0)
            rows = await self.session.query(
                f"SELECT {ARTIFACT_FIELDS} FROM Artifact WHERE {where_sql} ORDER BY @rid LIMIT :limit", params
            )
            return rows, total, {}

        lucene = lucene_query(q)
        if not lucene:
            return [], 0, {}
        params["q"] = lucene
        where_sql = " AND ".join(where)
        # rank all matches by (primary-field hit, score) using ids only, then load the page
        hits = await self.session.query(
            f"SELECT id, $score AS score FROM Artifact WHERE {where_sql} "
            f"AND SEARCH_INDEX('{ARTIFACT_INDEX}', :q) = true", params,
        )
        primary = {r["id"] for r in await self.session.query(
            f"SELECT id FROM Artifact WHERE {where_sql} AND SEARCH_INDEX('{ARTIFACT_PRIMARY_INDEX}', :q) = true",
            params,
        )}
        hits.sort(key=lambda r: (r["id"] in primary, r.get("score") or 0.0), reverse=True)
        page_ids = [r["id"] for r in hits[:limit]]
        rows = await self.session.query(
            f"SELECT {ARTIFACT_FIELDS} FROM Artifact WHERE user_id = :u AND id IN :ids",
            {"u": user_id, "ids": page_ids or [""]},
        )
        by_id = {r["id"]: r for r in rows}
        ordered = [by_id[i] for i in page_ids if i in by_id]

        terms = query_terms(q)
        snippets = {
            a["id"]: s for a in ordered
            if a["id"] not in primary and (s := snippet(a.get("content") or "", terms))
        }
        return ordered, len(hits), snippets
