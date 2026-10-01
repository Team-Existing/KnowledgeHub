from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.arcadedb import ArcadeSession


class UserRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        return await self.session.query_one("SELECT FROM User WHERE id = :id", {"id": user_id})

    async def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        return await self.session.query_one("SELECT FROM User WHERE username = :u", {"u": username})

    async def add(self, user: Dict[str, Any]) -> None:
        await self.session.execute("INSERT INTO User CONTENT :doc", {"doc": user})

    async def set_llm_model(self, user_id: str, model_id: Optional[str]) -> None:
        await self.session.execute(
            "UPDATE User SET llm_model = :m WHERE id = :id", {"m": model_id, "id": user_id}
        )

    async def using_model(self, model_id: str) -> List[Dict[str, Any]]:
        return await self.session.query("SELECT id FROM User WHERE llm_model = :m", {"m": model_id})
