from __future__ import annotations

from typing import Any, Dict, Iterable


def tag_fields(tags: Iterable[str] | None) -> Dict[str, Any]:
    """tags plus the derived fields used for full-text search and case-insensitive tag filters."""
    tags = [str(t) for t in (tags or [])]
    return {"tags": tags, "tags_text": " ".join(tags), "tags_lc": [t.lower() for t in tags]}
