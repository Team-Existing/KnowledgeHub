from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app import coordination
from app.db import get_client

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health() -> Dict[str, Any]:
    """
    Public. The load balancer routes only to instances answering 200 here, and
    the frontend shows a banner when the database is unreachable. "degraded"
    = still serving (some database node or the queue is down).
    """
    client = get_client()
    nodes = await client.node_status()
    queue = await coordination.ping()          # None in in-process mode
    any_node = any(n["ready"] for n in nodes)
    all_ok = all(n["ready"] for n in nodes) and queue is not False
    return {
        "status": "healthy" if all_ok else "degraded",
        "storage": "arcadedb",
        "database": client.database,
        "arcadedb": "connected" if any_node else "unreachable",
        "arcadedb_nodes": {"ready": sum(n["ready"] for n in nodes), "configured": len(nodes)},
        "background": "worker" if coordination.worker_mode() else "in-process",
        "queue": None if queue is None else ("connected" if queue else "unreachable"),
    }
