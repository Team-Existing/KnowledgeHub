"""Groups (shared spaces) and memberships."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.arcadedb import ArcadeSession
from app import activity

Row = Dict[str, Any]

# Membership.status
INVITED, ACTIVE, DECLINED, LEFT, REMOVED = "invited", "active", "declined", "left", "removed"
# Membership.role: the user who creates a group is its admin; everyone invited is a member
GROUP_ADMIN, GROUP_MEMBER = "admin", "member"


def membership_id(group_id: str, user_id: str) -> str:
    return "mem_" + hashlib.sha256(f"{group_id}:{user_id}".encode()).hexdigest()[:20]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GroupRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    # ── groups ──────────────────────────────────────────────────────────────

    async def get(self, group_id: str) -> Optional[Row]:
        return await self.session.query_one(
            "SELECT id, name, created_by, created_at, last_active_at FROM GroupSpace WHERE id = :id", {"id": group_id})

    async def add(self, group: Row) -> None:
        await self.session.execute("INSERT INTO GroupSpace CONTENT :doc", {"doc": group})

    async def rename(self, group_id: str, name: str) -> None:
        await self.session.execute("UPDATE GroupSpace SET name = :n WHERE id = :id", {"n": name, "id": group_id})

    async def delete_everything(self, group_id: str) -> None:
        """The group, its memberships, and everything stored in its space."""
        p = {"g": group_id}
        await self.session.execute("DELETE VERTEX FROM Node WHERE user_id = :g", p)   # edges go with them
        for doc in ("ItemEvent", "SyncedDocument", "Connector", "Playbook", "QueryLog"):
            await self.session.execute(f"DELETE FROM {doc} WHERE user_id = :g", p)
        await self.session.execute("DELETE FROM Membership WHERE group_id = :g", p)
        await self.session.execute("DELETE FROM GroupSpace WHERE id = :g", p)

    # ── memberships ─────────────────────────────────────────────────────────

    async def membership(self, group_id: str, user_id: str) -> Optional[Row]:
        return await self.session.query_one(
            "SELECT FROM Membership WHERE id = :id", {"id": membership_id(group_id, user_id)})

    async def set_membership(self, group_id: str, user_id: str, **fields: Any) -> None:
        mid = membership_id(group_id, user_id)
        await self.session.execute(
            "UPDATE Membership MERGE :doc UPSERT WHERE id = :id",
            {"id": mid, "doc": {"id": mid, "group_id": group_id, "user_id": user_id, **fields}},
        )

    async def members(self, group_id: str, statuses: List[str]) -> List[Row]:
        rows = await self.session.query(
            "SELECT user_id, role, status, invited_by, created_at, responded_at FROM Membership "
            "WHERE group_id = :g AND status IN :s ORDER BY created_at", {"g": group_id, "s": statuses})
        users = await self._usernames([r["user_id"] for r in rows] + [r["invited_by"] for r in rows if r.get("invited_by")])
        return [{**r, "username": users.get(r["user_id"], "?"),
                 "invited_by_username": users.get(r.get("invited_by") or "", None)} for r in rows]

    async def groups_of(self, user_id: str, status: str) -> List[Row]:
        """The user's groups with the given membership status, with group name and member count."""
        rows = await self.session.query(
            "SELECT group_id, role, status, invited_by, created_at FROM Membership "
            "WHERE user_id = :u AND status = :s ORDER BY created_at", {"u": user_id, "s": status})
        out: List[Row] = []
        users = await self._usernames([r["invited_by"] for r in rows if r.get("invited_by")])
        for r in rows:
            group = await self.get(r["group_id"])
            if not group:
                continue
            count = await self.session.scalar(
                "SELECT count(*) AS n FROM Membership WHERE group_id = :g AND status = 'active'", {"g": group["id"]}, 0)
            out.append({"id": group["id"], "name": group["name"], "role": r["role"], "status": r["status"],
                        "member_count": count, "created_at": group["created_at"],
                        "last_active_at": (activity.last_activity(group) or activity.now()).isoformat(),
                        "deletes_on": activity.deletes_on(group),
                        "invited_by": users.get(r.get("invited_by") or ""), "invited_at": r["created_at"]})
        return out

    async def search_users(self, text: str, exclude_user_id: str, limit: int = 20) -> List[Row]:
        return await self.session.query(
            "SELECT id, username FROM User WHERE username.toLowerCase() LIKE :q AND id <> :me "
            "ORDER BY username LIMIT :k",
            {"q": f"%{text.lower()}%", "me": exclude_user_id, "k": limit})

    async def _usernames(self, ids: List[str]) -> Dict[str, str]:
        ids = sorted({i for i in ids if i})
        if not ids:
            return {}
        rows = await self.session.query("SELECT id, username FROM User WHERE id IN :ids", {"ids": ids})
        return {r["id"]: r["username"] for r in rows}
