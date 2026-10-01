"""
Decision lineage: EVOLVES edges between items (newer -> older) and the
per-item event history (ItemEvent documents).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.arcadedb import ArcadeSession

Row = Dict[str, Any]

_EDGE_FIELDS = "@out.id AS src, @in.id AS dst, kind, note, origin, created_at"


def _edge(r: Row) -> Row:
    return {"from": r["src"], "to": r["dst"], "kind": r.get("kind"), "note": r.get("note") or "",
            "origin": r.get("origin") or "manual", "created_at": r.get("created_at")}


class LineageRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def list(self, user_id: str) -> List[Row]:
        rows = await self.session.query(
            f"SELECT {_EDGE_FIELDS} FROM EVOLVES WHERE user_id = :u ORDER BY @rid", {"u": user_id}
        )
        return [_edge(r) for r in rows]

    async def touching(self, user_id: str, item_id: str) -> List[Row]:
        rows = await self.session.query(
            f"SELECT {_EDGE_FIELDS} FROM EVOLVES WHERE user_id = :u AND (@out.id = :id OR @in.id = :id)",
            {"u": user_id, "id": item_id},
        )
        return [_edge(r) for r in rows]

    async def add(self, user_id: str, from_id: str, to_id: str, kind: str,
                  note: str = "", origin: str = "manual") -> bool:
        """Create from -EVOLVES-> to. Returns False if either item isn't the user's."""
        rows = await self.session.execute(
            "CREATE EDGE EVOLVES FROM (SELECT FROM KnowledgeItem WHERE id = :f AND user_id = :u) "
            "TO (SELECT FROM KnowledgeItem WHERE id = :t AND user_id = :u) "
            "SET user_id = :u, kind = :k, note = :n, origin = :o, created_at = :at",
            {"f": from_id, "t": to_id, "u": user_id, "k": kind, "n": note, "o": origin,
             "at": datetime.now(timezone.utc).isoformat()},
        )
        return bool(rows)

    async def remove(self, user_id: str, from_id: str, to_id: str) -> int:
        rows = await self.session.execute(
            "DELETE FROM EVOLVES WHERE user_id = :u AND @out.id = :f AND @in.id = :t",
            {"u": user_id, "f": from_id, "t": to_id},
        )
        return int(rows[0].get("count", 0)) if rows else 0


class ItemEventRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def add(self, user_id: str, item_id: str, kind: str, detail: Optional[Row] = None) -> None:
        await self.session.execute("INSERT INTO ItemEvent CONTENT :doc", {"doc": {
            "id": str(uuid.uuid4()), "user_id": user_id, "item_id": item_id, "kind": kind,
            "at": datetime.now(timezone.utc).isoformat(), "detail": detail or {},
        }})

    async def list(self, user_id: str, item_id: str) -> List[Row]:
        return await self.session.query(
            "SELECT kind, at, detail FROM ItemEvent WHERE item_id = :id AND user_id = :u ORDER BY at",
            {"id": item_id, "u": user_id},
        )
