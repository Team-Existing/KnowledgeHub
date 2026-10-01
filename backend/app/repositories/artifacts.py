from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.arcadedb import ArcadeSession
from app.repositories._common import tag_fields

Row = Dict[str, Any]

# everything except the (large) vectors
_FIELDS = ("id, user_id, title, content, source, source_type, author, tags, extraction_engine, "
           "created_at, metadata, summary")


class ArtifactRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def get_owned(self, user_id: str, artifact_id: str) -> Optional[Row]:
        return await self.session.query_one(
            f"SELECT {_FIELDS} FROM Artifact WHERE id = :id AND user_id = :u", {"id": artifact_id, "u": user_id}
        )

    async def list(self, user_id: str, source_type: Optional[str] = None) -> List[Row]:
        sql = f"SELECT {_FIELDS} FROM Artifact WHERE user_id = :u"
        params: Row = {"u": user_id}
        if source_type:
            sql += " AND source_type = :st"
            params["st"] = source_type
        return await self.session.query(sql + " ORDER BY @rid", params)

    async def ids_with_source_type(self, user_id: str, source_type: str) -> List[str]:
        rows = await self.session.query(
            "SELECT id FROM Artifact WHERE user_id = :u AND source_type = :st", {"u": user_id, "st": source_type}
        )
        return [r["id"] for r in rows]

    async def upsert(
        self,
        user_id: str,
        artifact_id: str,
        *,
        title: str,
        content: str,
        source: str,
        source_type: str,
        author: str,
        tags: List[str],
        created_at: str,
        metadata: Dict[str, Any],
        extraction_engine: str,
    ) -> None:
        """Insert, or refresh the mutable fields of an existing artifact (source/author/created_at stay)."""
        existing = await self.session.query_one(
            "SELECT id FROM Artifact WHERE id = :id AND user_id = :u", {"id": artifact_id, "u": user_id}
        )
        mutable = {"title": title, "content": content, "metadata": metadata,
                   "extraction_engine": extraction_engine, **tag_fields(tags)}
        if existing:
            await self.update(user_id, artifact_id, mutable)
            return
        await self.session.execute("INSERT INTO Artifact CONTENT :doc", {"doc": {
            "id": artifact_id, "user_id": user_id, "source": source, "source_type": source_type,
            "author": author, "created_at": created_at, **mutable,
        }})

    async def update(self, user_id: str, artifact_id: str, fields: Dict[str, Any]) -> None:
        if "tags" in fields:
            fields = {**fields, **tag_fields(fields["tags"])}
        await self.session.execute(
            "UPDATE Artifact MERGE :fields WHERE id = :id AND user_id = :u",
            {"fields": fields, "id": artifact_id, "u": user_id},
        )

    async def set_summary(
        self, user_id: str, artifact_id: str, summary: str,
        embedding: Optional[List[float]], provider: str, dims: int,
    ) -> None:
        await self.update(user_id, artifact_id, {
            "summary": summary, "summary_embedding": embedding,
            "summary_embedding_provider": provider, "summary_embedding_dims": dims,
        })

    async def summaries(self, user_id: str) -> List[Row]:
        return await self.session.query(
            "SELECT id, summary FROM Artifact WHERE user_id = :u AND summary IS NOT NULL", {"u": user_id}
        )

    async def delete_cascade(self, user_id: str, artifact_id: str) -> None:
        """Delete the artifact and its items; ArcadeDB removes their edges with them."""
        params = {"id": artifact_id, "u": user_id}
        await self.session.execute("DELETE VERTEX FROM KnowledgeItem WHERE artifact_id = :id AND user_id = :u", params)
        await self.session.execute("DELETE VERTEX FROM Artifact WHERE id = :id AND user_id = :u", params)
