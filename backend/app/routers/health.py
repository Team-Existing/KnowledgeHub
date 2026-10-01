from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app.db import get_client

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health() -> Dict[str, Any]:
    client = get_client()
    ready = await client.ready()
    return {
        "status": "healthy" if ready else "degraded",
        "storage": "arcadedb",
        "database": client.database,
        "arcadedb": "connected" if ready else "unreachable",
    }
