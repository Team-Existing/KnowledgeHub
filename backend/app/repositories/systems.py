"""
Systems (services, databases, teams …) and how they relate: AFFECTS edges from
knowledge items to the systems they concern, DEPENDS_ON edges between systems.
A System is its own vertex type, not a Node, so it stays out of the knowledge
graph and item queries; deleting a space deletes its systems explicitly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.arcadedb import ArcadeSession

Row = Dict[str, Any]

SYSTEM_KINDS = ("service", "database", "library", "infrastructure", "team", "product", "other")
_FIELDS = "id, name, kind, description, aliases, owner, created_at, created_by"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SystemRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    # ── systems ─────────────────────────────────────────────────────────────

    async def list(self, user_id: str) -> List[Row]:
        return await self.session.query(
            f"SELECT {_FIELDS} FROM System WHERE user_id = :u ORDER BY name", {"u": user_id})

    async def get(self, user_id: str, system_id: str) -> Optional[Row]:
        return await self.session.query_one(
            f"SELECT {_FIELDS} FROM System WHERE id = :id AND user_id = :u", {"id": system_id, "u": user_id})

    async def by_name(self, user_id: str, name: str) -> Optional[Row]:
        return await self.session.query_one(
            f"SELECT {_FIELDS} FROM System WHERE user_id = :u AND name.toLowerCase() = :n",
            {"u": user_id, "n": name.strip().lower()})

    async def create(self, user_id: str, fields: Row, actor: Optional[str]) -> Row:
        system = {"id": f"sys_{uuid.uuid4().hex[:16]}", "user_id": user_id, "created_at": _now(),
                  "created_by": actor, **fields}
        await self.session.execute("INSERT INTO System CONTENT :doc", {"doc": system})
        return system

    async def update(self, user_id: str, system_id: str, fields: Row) -> None:
        await self.session.execute("UPDATE System MERGE :doc WHERE id = :id AND user_id = :u",
                                   {"doc": fields, "id": system_id, "u": user_id})

    async def delete(self, user_id: str, system_id: str) -> None:
        await self.session.execute("DELETE VERTEX FROM System WHERE id = :id AND user_id = :u",
                                   {"id": system_id, "u": user_id})

    # ── edges ───────────────────────────────────────────────────────────────

    async def dependencies(self, user_id: str) -> List[Row]:
        """Every DEPENDS_ON edge in the space: `from` depends on `to`."""
        return [{"from": r["src"], "to": r["dst"], "note": r.get("note") or ""} for r in await self.session.query(
            "SELECT @out.id AS src, @in.id AS dst, note FROM DEPENDS_ON WHERE user_id = :u", {"u": user_id})]

    async def add_dependency(self, user_id: str, from_id: str, to_id: str, note: str) -> bool:
        rows = await self.session.execute(
            "CREATE EDGE DEPENDS_ON FROM (SELECT FROM System WHERE id = :f AND user_id = :u) "
            "TO (SELECT FROM System WHERE id = :t AND user_id = :u) IF NOT EXISTS "
            "SET user_id = :u, note = :n, created_at = :at",
            {"f": from_id, "t": to_id, "u": user_id, "n": note, "at": _now()})
        return bool(rows)

    async def remove_dependency(self, user_id: str, from_id: str, to_id: str) -> None:
        await self.session.execute(
            "DELETE FROM DEPENDS_ON WHERE user_id = :u AND @out.id = :f AND @in.id = :t",
            {"u": user_id, "f": from_id, "t": to_id})

    async def affects(self, user_id: str) -> List[Row]:
        """Every AFFECTS edge in the space: item -> system."""
        return [{"item_id": r["src"], "system_id": r["dst"], "origin": r.get("origin") or "manual"}
                for r in await self.session.query(
                    "SELECT @out.id AS src, @in.id AS dst, origin FROM AFFECTS WHERE user_id = :u", {"u": user_id})]

    async def link_item(self, user_id: str, item_id: str, system_id: str, origin: str = "manual") -> bool:
        rows = await self.session.execute(
            "CREATE EDGE AFFECTS FROM (SELECT FROM KnowledgeItem WHERE id = :i AND user_id = :u) "
            "TO (SELECT FROM System WHERE id = :s AND user_id = :u) IF NOT EXISTS "
            "SET user_id = :u, origin = :o, created_at = :at",
            {"i": item_id, "s": system_id, "u": user_id, "o": origin, "at": _now()})
        return bool(rows)

    async def unlink_item(self, user_id: str, item_id: str, system_id: str) -> None:
        await self.session.execute(
            "DELETE FROM AFFECTS WHERE user_id = :u AND @out.id = :i AND @in.id = :s",
            {"u": user_id, "i": item_id, "s": system_id})


async def delete_space_systems(session: ArcadeSession, space_id: str) -> None:
    """Part of deleting a space: its systems, and with them their edges."""
    await session.execute("DELETE VERTEX FROM System WHERE user_id = :s", {"s": space_id})
