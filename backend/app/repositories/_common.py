from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


def tag_fields(tags: Iterable[str] | None) -> Dict[str, Any]:
    """tags plus the derived fields used for full-text search and case-insensitive tag filters."""
    tags = [str(t) for t in (tags or [])]
    return {"tags": tags, "tags_text": " ".join(tags), "tags_lc": [t.lower() for t in tags]}


def page(sql: str, *, newest_first: bool, limit: Optional[int], offset: int = 0, newest_by: str = "created_at") -> str:
    """
    Order and page. newest_first sorts by the `newest_by` timestamp, then id (so pages
    never overlap); otherwise by @rid as before. Not by @rid for "newest": ArcadeDB
    spreads a type's records over several buckets, so @rid order isn't insertion order.
    limit/offset are ints, validated by the caller.
    """
    sql += f" ORDER BY {newest_by} DESC, id DESC" if newest_first else " ORDER BY @rid"
    if offset:
        sql += f" SKIP {int(offset)}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql


def contains_pattern(q: str) -> str:
    """A case-insensitive LIKE pattern for "contains q" (the value is bound, not inlined)."""
    return f"%{q.strip().lower()}%"
