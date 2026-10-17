"""
Graph edges. The API exposes them as {"from", "to", "type"}:
  CONTAINS    artifact -> item                 type "CONTAINS"
  RELATED_TO  item -> item (cross-links)        type "RELATED_TO"
  LINK        node -> node (OKF import)         type = the imported relation label
  EVOLVES     item -> item (decision lineage)   type = the kind, upper-cased ("SUPERSEDES", ...)
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.arcadedb import ArcadeSession

Row = Dict[str, Any]

EDGE_TYPES = ("CONTAINS", "RELATED_TO", "LINK", "EVOLVES")


class RelationshipRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def touching(
        self, user_id: str, ids: Iterable[str], edge_types: Iterable[str] = EDGE_TYPES,
    ) -> List[Row]:
        """
        The edges at the given nodes, found by walking out from them (the id index finds
        the nodes). Filtering every edge of the space on its ends instead took a second
        on 6,000 items. An artifact can hold thousands of items, so pass item ids, and
        artifacts only with edge_types=("LINK",), unless all of its CONTAINS edges are wanted.
        """
        ids = list(ids)
        types = [t for t in edge_types if t in EDGE_TYPES]
        if not ids or not types:
            return []
        rows = await self.session.query(
            "SELECT @type AS edge_type, @out.id AS src, @in.id AS dst, type AS link_type, kind "
            f"FROM (SELECT expand(bothE({', '.join(repr(t) for t in types)})) FROM Node "
            "WHERE id IN :ids AND user_id = :u)",
            {"u": user_id, "ids": ids},
        )
        rels: List[Row] = []
        seen = set()
        for r in rows:
            edge_type = r["edge_type"]
            label = {"LINK": r.get("link_type") or "", "EVOLVES": (r.get("kind") or "").upper()}.get(edge_type, edge_type)
            key = (r["src"], r["dst"], edge_type, label)
            if key not in seen:            # an edge between two of the nodes is reached from both ends
                seen.add(key)
                rels.append({"from": r["src"], "to": r["dst"], "type": label})
        return rels

    async def among(self, user_id: str, item_ids: Iterable[str], artifact_ids: Iterable[str]) -> List[Row]:
        """The edges between the given items and artifacts (both ends among them)."""
        item_ids, artifact_ids = list(item_ids), list(artifact_ids)
        shown = set(item_ids) | set(artifact_ids)
        found = await self.touching(user_id, item_ids) + await self.touching(user_id, artifact_ids, ("LINK",))
        out, seen = [], set()
        for r in found:
            key = (r["from"], r["to"], r["type"])
            if r["from"] in shown and r["to"] in shown and key not in seen:
                seen.add(key)
                out.append(r)
        return out

    async def list(self, user_id: str) -> List[Row]:
        """Every edge in the space."""
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
