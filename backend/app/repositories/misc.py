"""Cross-links (RELATED_TO edges), playbooks and query logs."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Tuple

from app.arcadedb import ArcadeSession

Row = Dict[str, Any]


class CrossLinkRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def list(self, user_id: str) -> List[Row]:
        return await self.session.query(
            "SELECT @out.id AS item_id_a, @in.id AS item_id_b, score FROM RELATED_TO "
            "WHERE user_id = :u ORDER BY @rid", {"u": user_id},
        )

    async def replace_all(self, user_id: str, links: List[Tuple[str, str, float]]) -> None:
        await self.session.execute("DELETE FROM RELATED_TO WHERE user_id = :u", {"u": user_id})
        for id_a, id_b, score in links:
            await self.session.execute(
                "CREATE EDGE RELATED_TO FROM (SELECT FROM KnowledgeItem WHERE id = :a AND user_id = :u) "
                "TO (SELECT FROM KnowledgeItem WHERE id = :b AND user_id = :u) SET user_id = :u, score = :s",
                {"a": id_a, "b": id_b, "u": user_id, "s": float(score)},
            )


class PlaybookRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def list(self, user_id: str) -> List[Row]:
        return await self.session.query(
            "SELECT id, title, steps, category FROM Playbook WHERE user_id = :u ORDER BY @rid", {"u": user_id}
        )

    async def add(self, user_id: str, playbook: Row) -> None:
        # ids derive from the title, so saving the same title again replaces it
        await self.session.execute(
            "UPDATE Playbook SET id = :id, user_id = :u, title = :t, steps = :s, category = :c "
            "UPSERT WHERE id = :id AND user_id = :u",
            {"id": playbook["id"], "u": user_id, "t": playbook["title"],
             "s": playbook["steps"], "c": playbook["category"]},
        )


class QueryLogRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def add(self, **fields: Any) -> None:
        await self.session.execute("INSERT INTO QueryLog CONTENT :doc", {"doc": {"id": str(uuid.uuid4()), **fields}})

    async def find(self, user_id: str, question: str) -> List[Row]:
        return await self.session.query(
            "SELECT FROM QueryLog WHERE user_id = :u AND question = :q", {"u": user_id, "q": question}
        )
