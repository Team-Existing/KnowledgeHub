"""
Graph edges. The API exposes them as {"from", "to", "type"}:
  CONTAINS    artifact -> item                 type "CONTAINS"
  RELATED_TO  item -> item (cross-links)        type "RELATED_TO"
  LINK        node -> node (OKF import)         type = the imported relation label
  EVOLVES     item -> item (decision lineage)   type = the kind, upper-cased ("SUPERSEDES", ...)
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.arcadedb import ArcadeSession

Row = Dict[str, Any]


class RelationshipRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def list(self, user_id: str) -> List[Row]:
        rels: List[Row] = []
        for edge_type, label in (("CONTAINS", "'CONTAINS'"), ("RELATED_TO", "'RELATED_TO'"),
                                 ("LINK", "type"), ("EVOLVES", "kind")):
            rows = await self.session.query(
                f"SELECT @out.id AS src, @in.id AS dst, {label} AS kind FROM {edge_type} "
                "WHERE user_id = :u ORDER BY @rid", {"u": user_id},
            )
            upper = edge_type == "EVOLVES"
            rels += [{"from": r["src"], "to": r["dst"], "type": (r["kind"] or "").upper() if upper else r["kind"]}
                     for r in rows]
        return rels

    async def ensure_contains(self, user_id: str, artifact_id: str, item_ids: List[str]) -> List[Row]:
        """Add any missing artifact -CONTAINS-> item edges (one statement); return the full edge list."""
        if item_ids:
            await self.session.execute(
                "CREATE EDGE CONTAINS FROM (SELECT FROM Artifact WHERE id = :a AND user_id = :u) "
                "TO (SELECT FROM KnowledgeItem WHERE id IN :ids AND user_id = :u) IF NOT EXISTS SET user_id = :u",
                {"a": artifact_id, "ids": item_ids, "u": user_id},
            )
        return [{"from": artifact_id, "to": item_id, "type": "CONTAINS"} for item_id in item_ids]

    async def add_link(self, user_id: str, from_id: str, to_id: str, label: str) -> bool:
        """Link two of the user's nodes (artifacts or items). Returns False if either doesn't exist."""
        rows = await self.session.execute(
            "CREATE EDGE LINK FROM (SELECT FROM Node WHERE id = :f AND user_id = :u) "
            "TO (SELECT FROM Node WHERE id = :t AND user_id = :u) IF NOT EXISTS SET user_id = :u, type = :label",
            {"f": from_id, "t": to_id, "u": user_id, "label": label},
        )
        return bool(rows)
