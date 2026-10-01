from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


class IngestionNormalization:

    def normalize_format(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize content type to plain text. HTML tags are stripped; other types pass through."""
        content_type = artifact.get("type", "text")
        if content_type == "html":
            artifact["content"] = self._html_to_text(artifact["content"])
        artifact["normalized_type"] = "text"
        return artifact

    def extract_metadata(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "author": artifact.get("author", "unknown"),
            "date": artifact.get("created_at", datetime.now(timezone.utc).isoformat()),
            "tags": artifact.get("tags", []),
            "source": artifact.get("source"),
        }

    # ── internal ──────────────────────────────────────────────────────────

    @staticmethod
    def _html_to_text(html: str) -> str:
        import re
        return re.sub(r"<[^>]+>", " ", html).strip()
