"""Database rows → API dicts (same response shapes as before the ArcadeDB move)."""
from __future__ import annotations

from typing import Any, Dict

Row = Dict[str, Any]


def artifact_dict(a: Row) -> Row:
    return {
        "id": a["id"],
        "title": a.get("title"),
        "content": a.get("content"),
        "source": a.get("source"),
        "source_type": a.get("source_type") or "manual",
        "author": a.get("author"),
        "tags": a.get("tags") or [],
        "extraction_engine": a.get("extraction_engine") or "regex",
        "created_at": a.get("created_at"),
        "metadata": a.get("metadata") or {},
    }


def item_dict(i: Row) -> Row:
    return {
        "id": i["id"],
        "artifact_id": i.get("artifact_id"),
        "title": i.get("title"),
        "type": i.get("type"),
        "author": i.get("author"),
        "date": i.get("date"),
        "tags": i.get("tags") or [],
        "details": i.get("details") or {},
        "extraction_engine": i.get("extraction_engine") or "regex",
        "review_status": i.get("review_status"),
        "review_note": i.get("review_note") or "",
        # lifecycle of decisions and risks (see services/lineage.py); None for other types
        "status": i.get("status"),
        "declared_status": i.get("declared_status"),
    }


def playbook_dict(p: Row) -> Row:
    return {"id": p["id"], "title": p.get("title"), "steps": p.get("steps") or [], "category": p.get("category")}
