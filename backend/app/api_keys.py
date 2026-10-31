"""
Read-only API keys, for internal developer portals and other tools that show
a space's knowledge where engineers work (Backstage plugins, wikis, CI checks).

A key (khk_…) belongs to one space and acts as the person who created it, who
must still be a member of that space. It only works on READ_ONLY routes:
search, items, decisions and their history, GraphRAG questions, impact
analysis, commitments, health and systems. Everything else answers 403, so a
leaked key can read what its creator could read in that space, and change
nothing. Only a SHA-256 hash is stored; the key is shown once, when created.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.arcadedb import ArcadeSession

Row = Dict[str, Any]

PREFIX = "khk_"

# (method, path pattern) a key may call. POSTs here only read: they answer a question.
READ_ONLY = [(m, re.compile(p)) for m, p in (
    ("GET", r"/knowledge"),
    ("GET", r"/knowledge/search"),
    ("GET", r"/knowledge/register"),
    ("GET", r"/knowledge/items/[^/]+"),
    ("GET", r"/knowledge/items/[^/]+/(lineage|context|systems)"),
    ("GET", r"/knowledge/health"),
    ("GET", r"/knowledge/commitments"),
    ("GET", r"/knowledge/outcomes"),
    ("GET", r"/knowledge/systems"),
    ("GET", r"/knowledge/learning(/.*)?"),
    ("POST", r"/knowledge/graphrag/query"),
    ("POST", r"/knowledge/impact"),
    ("GET", r"/auth/me"),
)]


def allowed(method: str, path: str) -> bool:
    return any(method == m and pattern.fullmatch(path.rstrip("/") or "/") for m, pattern in READ_ONLY)


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def lookup(session: ArcadeSession, key: str) -> Optional[Row]:
    return await session.query_one("SELECT id, user_id, created_by FROM ApiKey WHERE key_hash = :h", {"h": _hash(key)})


async def touch(session: ArcadeSession, key_id: str) -> None:
    await session.execute("UPDATE ApiKey SET last_used_at = :t WHERE id = :id", {"t": _now(), "id": key_id})
    await session.commit()


async def list_keys(session: ArcadeSession, space_id: str) -> List[Row]:
    return await session.query(
        "SELECT id, name, hint, created_by_name, created_at, last_used_at FROM ApiKey WHERE user_id = :u "
        "ORDER BY created_at", {"u": space_id})


async def create(session: ArcadeSession, space_id: str, name: str, user_id: str, username: str) -> Row:
    key = PREFIX + secrets.token_urlsafe(32)
    row = {"id": f"apk_{uuid.uuid4().hex[:16]}", "user_id": space_id, "name": name, "key_hash": _hash(key),
           "hint": key[:8] + "…" + key[-4:], "created_by": user_id, "created_by_name": username, "created_at": _now()}
    await session.execute("INSERT INTO ApiKey CONTENT :doc", {"doc": row})
    await session.commit()
    return {k: row[k] for k in ("id", "name", "hint", "created_by_name", "created_at")} | {"key": key, "last_used_at": None}


async def delete(session: ArcadeSession, space_id: str, key_id: str) -> bool:
    found = await session.query_one("SELECT id FROM ApiKey WHERE id = :id AND user_id = :u", {"id": key_id, "u": space_id})
    if found:
        await session.execute("DELETE FROM ApiKey WHERE id = :id", {"id": key_id})
        await session.commit()
    return bool(found)
