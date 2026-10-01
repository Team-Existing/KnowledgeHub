"""
Graph + vector retrieval for GraphRAG and the graph view.

Vector search is two-stage. LSM_VECTOR indexes are shared by all users and
can't pre-filter, so the ANN query over-fetches candidates and keeps the
user's own (non-rejected) nodes. If that yields at least k results they are
the user's true top k (everything else they own is farther away than every
candidate). If it yields fewer — typically a user who owns a small share of
the data, whose items all fall outside the global candidate set — the query
falls back to an exact cosine scan over just that user's vectors, which is
cheap precisely because they have few.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.arcadedb import ArcadeSession
from app.repositories.search import ITEM_INDEX, lucene_query

Row = Dict[str, Any]
EDGE_TYPES = "'CONTAINS', 'RELATED_TO', 'LINK', 'EVOLVES'"


def candidate_count(top_k: int) -> int:
    return max(top_k * 10, 100)


def _cosine_score(distance: Any) -> float:
    # LSM_VECTOR COSINE reports distance = 1 - cosine similarity
    return round(1.0 - float(distance or 0.0), 6)


class GraphStore:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def _nearest(
        self, type_name: str, prop: str, fields: str, where: str,
        user_id: str, vector: List[float], top_k: int,
    ) -> List[Row]:
        params: Row = {"v": vector, "u": user_id, "k": top_k}
        rows = await self.session.query(
            f"SELECT {fields}, distance FROM (SELECT expand(vectorNeighbors('{type_name}[{prop}]', :v, :candidates))) "
            f"WHERE user_id = :u {where} ORDER BY distance LIMIT :k",
            {**params, "candidates": candidate_count(top_k)},
        )
        if len(rows) >= top_k:
            return [{**r, "score": _cosine_score(r.pop("distance"))} for r in rows]
        # too few of this user's nodes among the global candidates: exact scan of their own vectors
        return await self.session.query(
            f"SELECT {fields}, vectorCosineSimilarity({prop}, :v) AS score FROM {type_name} "
            f"WHERE user_id = :u AND {prop} IS NOT NULL {where} ORDER BY score DESC LIMIT :k",
            params,
        )

    async def vector_search(self, user_id: str, vector: List[float], top_k: int = 8) -> List[Row]:
        return await self._nearest(
            "KnowledgeItem", "embedding", "id, title, type AS kind, artifact_id, tags, status",
            "AND review_status <> 'rejected'", user_id, vector, top_k,
        )

    async def summary_search(self, user_id: str, vector: List[float], top_k: int = 3) -> List[Row]:
        return await self._nearest(
            "Artifact", "summary_embedding", "id AS artifact_id, title, summary", "", user_id, vector, top_k,
        )

    async def keyword_search(self, user_id: str, text: str, top_k: int = 8) -> List[Row]:
        """Full-text match on any query word (OR), for recall alongside vector search."""
        lucene = lucene_query(text, require_all=False)
        if not lucene:
            return []
        return await self.session.query(
            "SELECT id, title, type AS kind, artifact_id, tags, status, $score AS score FROM KnowledgeItem "
            f"WHERE user_id = :u AND review_status <> 'rejected' AND SEARCH_INDEX('{ITEM_INDEX}', :q) = true "
            "ORDER BY $score DESC LIMIT :k",
            {"u": user_id, "q": lucene, "k": top_k},
        )

    async def graph_expand(self, user_id: str, item_ids: List[str]) -> List[Row]:
        """One hop along CONTAINS / RELATED_TO / LINK edges from the seed items."""
        if not item_ids:
            return []
        rows = await self.session.query(
            "SELECT id, title, type, artifact_id, review_status, status, @type AS node_type "
            f"FROM (SELECT expand(both({EDGE_TYPES})) FROM KnowledgeItem WHERE id IN :ids AND user_id = :u) "
            "WHERE user_id = :u",
            {"ids": item_ids, "u": user_id},
        )
        out, seen = [], set()
        for r in rows:
            if r["id"] in seen or r.get("review_status") == "rejected":
                continue
            seen.add(r["id"])
            is_artifact = r.get("node_type") == "Artifact"
            out.append({
                "id": r["id"],
                "title": r.get("title"),
                "kind": "artifact" if is_artifact else r.get("type"),
                "artifact_id": None if is_artifact else r.get("artifact_id"),
                "status": None if is_artifact else r.get("status"),
                "labels": [r.get("node_type")],
            })
        return out

    async def successors(self, user_id: str, item_ids: List[str]) -> Dict[str, List[Row]]:
        """For each item, the items that supersede or reverse it: {old_id: [{id, title, kind}]}."""
        if not item_ids:
            return {}
        rows = await self.session.query(
            "SELECT @in.id AS old, @out.id AS id, @out.title AS title, kind FROM EVOLVES "
            "WHERE user_id = :u AND kind IN ['supersedes', 'reverses'] AND @in.id IN :ids",
            {"u": user_id, "ids": item_ids},
        )
        out: Dict[str, List[Row]] = {}
        for r in rows:
            out.setdefault(r["old"], []).append({"id": r["id"], "title": r.get("title"), "kind": r["kind"]})
        return out

    async def retrieve_for_rag(self, user_id: str, vector: List[float], top_k: int = 8) -> List[Row]:
        """
        Vector seeds plus their graph neighbourhood. Seeds keep their cosine
        score; neighbours get the best seed score discounted by 0.7.
        """
        seeds = await self.vector_search(user_id, vector, top_k)
        if not seeds:
            return []
        best = max(s["score"] for s in seeds)
        combined = [{**s, "retrieved_by": "vector"} for s in seeds]
        seen = {s["id"] for s in seeds}
        for node in await self.graph_expand(user_id, list(seen)):
            if node["id"] not in seen:
                seen.add(node["id"])
                combined.append({**node, "retrieved_by": "graph", "score": best * 0.7})
        combined.sort(key=lambda n: n.get("score") or 0.0, reverse=True)
        return combined

    async def visualization(self, user_id: str) -> Row:
        from app.repositories.relationships import RelationshipRepository

        nodes = [
            {"id": r["id"], "label": r.get("title") or r["id"],
             "type": "artifact" if r["node_type"] == "Artifact" else r.get("type")}
            for r in await self.session.query(
                "SELECT id, title, type, @type AS node_type FROM Node WHERE user_id = :u ORDER BY @rid",
                {"u": user_id},
            )
        ]
        edges = [
            {"source": e["from"], "target": e["to"], "label": e["type"]}
            for e in await RelationshipRepository(self.session).list(user_id)
        ]
        return {"nodes": nodes, "edges": edges, "layout": "force-directed"}
