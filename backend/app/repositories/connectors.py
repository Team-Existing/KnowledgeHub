"""Connectors (external knowledge sources) and the documents each has synced."""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from app.arcadedb import ArcadeSession

Row = Dict[str, Any]

_FIELDS = ("id, user_id, kind, name, config, created_at, created_by_name, last_sync_at, last_status, "
           "last_error, last_result")


def synced_document_id(connector_id: str, external_id: str) -> str:
    return "sync_" + hashlib.sha256(f"{connector_id}:{external_id}".encode()).hexdigest()[:20]


class ConnectorRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    async def list(self, user_id: str) -> List[Row]:
        return await self.session.query(
            f"SELECT {_FIELDS} FROM Connector WHERE user_id = :u ORDER BY created_at", {"u": user_id}
        )

    async def get_owned(self, user_id: str, connector_id: str) -> Optional[Row]:
        return await self.session.query_one(
            f"SELECT {_FIELDS} FROM Connector WHERE id = :id AND user_id = :u", {"id": connector_id, "u": user_id}
        )

    async def add(self, connector: Row) -> None:
        await self.session.execute("INSERT INTO Connector CONTENT :doc", {"doc": connector})

    async def update(self, user_id: str, connector_id: str, fields: Row) -> None:
        await self.session.execute(
            "UPDATE Connector MERGE :fields WHERE id = :id AND user_id = :u",
            {"fields": fields, "id": connector_id, "u": user_id},
        )

    async def delete(self, user_id: str, connector_id: str) -> None:
        params = {"id": connector_id, "u": user_id}
        await self.session.execute("DELETE FROM SyncedDocument WHERE connector_id = :id AND user_id = :u", params)
        await self.session.execute("DELETE FROM Connector WHERE id = :id AND user_id = :u", params)

    # ── synced documents ────────────────────────────────────────────────────

    async def documents(self, user_id: str, connector_id: str) -> List[Row]:
        return await self.session.query(
            "SELECT external_id, artifact_id, content_hash, title, url, synced_at FROM SyncedDocument "
            "WHERE connector_id = :c AND user_id = :u ORDER BY synced_at DESC",
            {"c": connector_id, "u": user_id},
        )

    async def document_hashes(self, user_id: str, connector_id: str) -> Dict[str, str]:
        return {d["external_id"]: d.get("content_hash") or "" for d in await self.documents(user_id, connector_id)}

    async def artifact_ids(self, user_id: str, connector_id: str) -> List[str]:
        return [d["artifact_id"] for d in await self.documents(user_id, connector_id) if d.get("artifact_id")]

    async def record_document(self, user_id: str, connector_id: str, doc: Row) -> None:
        doc_id = synced_document_id(connector_id, doc["external_id"])
        await self.session.execute(
            "UPDATE SyncedDocument MERGE :doc UPSERT WHERE id = :id",
            {"id": doc_id, "doc": {"id": doc_id, "user_id": user_id, "connector_id": connector_id, **doc}},
        )
