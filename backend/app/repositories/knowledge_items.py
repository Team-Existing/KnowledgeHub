from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.arcadedb import ArcadeSession
from app.repositories._common import tag_fields

Row = Dict[str, Any]

# everything except the embedding vector
_FIELDS = ("id, user_id, artifact_id, title, type, author, date, tags, details, extraction_engine, "
           "review_status, review_note, embedding_provider, status, declared_status")


class KnowledgeItemRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def get_owned(self, user_id: str, item_id: str) -> Optional[Row]:
        return await self.session.query_one(
            f"SELECT {_FIELDS} FROM KnowledgeItem WHERE id = :id AND user_id = :u", {"id": item_id, "u": user_id}
        )

    async def list(
        self,
        user_id: str,
        *,
        type: Optional[str] = None,
        review_status: Optional[str] = None,
        exclude_rejected: bool = False,
        with_embedding: bool = False,
    ) -> List[Row]:
        fields = _FIELDS + (", embedding" if with_embedding else "")
        sql = f"SELECT {fields} FROM KnowledgeItem WHERE user_id = :u"
        params: Row = {"u": user_id}
        if type:
            sql += " AND type = :type"
            params["type"] = type
        if review_status:
            sql += " AND review_status = :rs"
            params["rs"] = review_status
        if exclude_rejected:
            sql += " AND review_status <> 'rejected'"
        return await self.session.query(sql + " ORDER BY @rid", params)

    async def count_embedded(self, user_id: str) -> int:
        return await self.session.scalar(
            "SELECT count(*) AS n FROM KnowledgeItem WHERE user_id = :u AND embedding IS NOT NULL", {"u": user_id}, 0
        )

    async def find_by_ref(self, user_id: str, ref: str) -> Optional[Row]:
        """Resolve a loose reference: exact id, then id suffix, then title substring (first match)."""
        item = await self.get_owned(user_id, ref)
        if item:
            return item
        suffix = ref.split("_")[-1]
        for condition, value in (("id LIKE :v", f"%{suffix}"), ("title.toLowerCase() LIKE :v", f"%{ref.lower()}%")):
            item = await self.session.query_one(
                f"SELECT {_FIELDS} FROM KnowledgeItem WHERE user_id = :u AND {condition} ORDER BY @rid LIMIT 1",
                {"u": user_id, "v": value},
            )
            if item:
                return item
        return None

    async def upsert(self, user_id: str, item: Row, *, extraction_engine: Optional[str] = None) -> None:
        """
        Insert a new item (review_status=pending unless the item says otherwise), or
        refresh title/details/tags of an existing one. Review state and other user
        edits are kept.
        """
        existing = await self.session.query_one(
            "SELECT id FROM KnowledgeItem WHERE id = :id AND user_id = :u", {"id": item["id"], "u": user_id}
        )
        if existing:
            fields: Row = {"title": item["title"], "details": item["details"], "tags": item["tags"]}
            if extraction_engine is not None:
                fields["extraction_engine"] = extraction_engine
            await self.update(user_id, item["id"], fields)
            return
        await self.session.execute("INSERT INTO KnowledgeItem CONTENT :doc", {"doc": {
            "id": item["id"],
            "user_id": user_id,
            "artifact_id": item["artifact_id"],
            "title": item["title"],
            "type": item["type"],
            "author": item["author"],
            "date": item["date"],
            "details": item["details"],
            "extraction_engine": extraction_engine or "regex",
            "review_status": item.get("review_status") or "pending",
            "review_note": "",
            **tag_fields(item["tags"]),
        }})

    async def update(self, user_id: str, item_id: str, fields: Row) -> None:
        if "tags" in fields:
            fields = {**fields, **tag_fields(fields["tags"])}
        await self.session.execute(
            "UPDATE KnowledgeItem MERGE :fields WHERE id = :id AND user_id = :u",
            {"fields": fields, "id": item_id, "u": user_id},
        )

    async def sync_for_artifact(
        self, user_id: str, artifact_id: str, items: List[Row], extraction_engine: str,
    ) -> None:
        """
        Make the artifact's stored items match `items`: drop stale ones, upsert
        the rest. An item's own "extraction_engine" overrides the default.
        """
        await self.session.execute(
            "DELETE VERTEX FROM KnowledgeItem WHERE artifact_id = :a AND user_id = :u AND id NOT IN :keep",
            {"a": artifact_id, "u": user_id, "keep": [i["id"] for i in items] or [""]},
        )
        for item in items:
            await self.upsert(user_id, item, extraction_engine=item.get("extraction_engine", extraction_engine))

    async def set_embedding(
        self, user_id: str, item_id: str, vector: List[float], provider: str, dims: int,
    ) -> bool:
        rows = await self.session.execute(
            "UPDATE KnowledgeItem SET embedding = :e, embedding_provider = :p, embedding_dims = :d "
            "WHERE id = :id AND user_id = :u",
            {"e": vector, "p": provider, "d": dims, "id": item_id, "u": user_id},
        )
        return bool(rows and rows[0].get("count"))

    async def delete(self, user_id: str, item_id: str) -> None:
        """Deleting the vertex also removes every edge touching it."""
        await self.session.execute(
            "DELETE VERTEX FROM KnowledgeItem WHERE id = :id AND user_id = :u", {"id": item_id, "u": user_id}
        )
