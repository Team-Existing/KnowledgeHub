"""ArcadeDB connection lifecycle and the per-request session dependency."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

from app.arcadedb import ArcadeClient, ArcadeSession
from app.schema import apply_schema

_client: Optional[ArcadeClient] = None


@dataclass(frozen=True)
class User:
    id: str
    username: str
    role: str = "member"
    llm_model: Optional[str] = None   # the user's chosen local LLM (see services/llm_catalog.py)


def get_client() -> ArcadeClient:
    if _client is None:
        raise RuntimeError("ArcadeDB is not initialised; init_db() runs at application startup")
    return _client


def client_from_env() -> ArcadeClient:
    """A client for the configured database. ARCADEDB_URL may list several cluster nodes, comma-separated."""
    return ArcadeClient(
        url=os.getenv("ARCADEDB_URL", "http://localhost:2480"),
        database=os.getenv("ARCADEDB_DATABASE", "knowledge_hubs"),
        user=os.getenv("ARCADEDB_USER", "root"),
        password=os.getenv("ARCADEDB_PASSWORD", ""),
    )


def migrate_on_startup() -> bool:
    """
    Whether this process applies the schema and migrations when it starts.
    Single-instance development: yes (the default). Several app instances:
    no; the provisioning job does it once, so instances don't race each other.
    """
    return os.getenv("DB_MIGRATE_ON_STARTUP", "true").strip().lower() not in ("false", "0", "no", "off")


async def init_db(embedding_dims: int, embedding_provider: str) -> None:
    """Connect; with DB_MIGRATE_ON_STARTUP, also create the database if needed and apply the schema."""
    global _client
    _client = client_from_env()
    if migrate_on_startup():
        await _client.ensure_database()
        await apply_schema(_client, embedding_dims, embedding_provider)
    elif not await _client.database_exists():
        raise RuntimeError(f"database '{_client.database}' does not exist yet; run the provisioning job first")


async def close_db() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def get_session() -> AsyncGenerator[ArcadeSession, None]:
    session = ArcadeSession(get_client())
    try:
        yield session
    finally:
        await session.close()   # rolls back anything left uncommitted
