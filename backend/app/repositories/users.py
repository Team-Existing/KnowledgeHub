from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.arcadedb import ArcadeSession
from app.usernames import username_key


class UserRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        return await self.session.query_one("SELECT FROM User WHERE id = :id", {"id": user_id})

    async def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Case-insensitive (see app/usernames.py). An exact match wins, which keeps legacy
        accounts that differ only in case (from before names were unique) reachable."""
        exact = await self.session.query_one("SELECT FROM User WHERE username = :u", {"u": username})
        if exact:
            return exact
        return await self.session.query_one(
            "SELECT FROM User WHERE username_key = :k ORDER BY created_at LIMIT 1", {"k": username_key(username)})

    async def add(self, user: Dict[str, Any]) -> None:
        await self.session.execute("INSERT INTO User CONTENT :doc", {"doc": user})

    async def set_llm_model(self, user_id: str, model_id: Optional[str]) -> None:
        await self.session.execute(
            "UPDATE User SET llm_model = :m WHERE id = :id", {"m": model_id, "id": user_id}
        )

    async def using_model(self, model_id: str) -> List[Dict[str, Any]]:
        return await self.session.query("SELECT id FROM User WHERE llm_model = :m", {"m": model_id})

    async def list(self) -> List[Dict[str, Any]]:
        return await self.session.query(
            "SELECT id, username, role, created_at, llm_model, last_active_at, last_login_at FROM User ORDER BY created_at"
        )

    async def set_role(self, user_id: str, role: str) -> None:
        await self.session.execute("UPDATE User SET role = :r WHERE id = :id", {"r": role, "id": user_id})

    async def count_with_role(self, role: str) -> int:
        return await self.session.scalar("SELECT count(*) AS n FROM User WHERE role = :r", {"r": role}, 0)
