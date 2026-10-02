from __future__ import annotations

import hashlib
from typing import Any, Dict, List


class CurationLayer:
    """
    Playbooks: a named, ordered list of steps, optionally pointing at knowledge
    items (e.g. how-tos and checklists). The id comes from the space and the
    title, so saving a playbook with the same title replaces it.
    """

    def build_playbook(self, space_id: str, title: str, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        title = title.strip()
        digest = hashlib.sha256(f"{space_id}:{title.casefold()}".encode()).hexdigest()[:12]
        return {
            "id": f"playbook_{digest}",
            "title": title,
            "steps": self.normalize_steps(steps),
            "category": self._categorize(title),
        }

    @staticmethod
    def normalize_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Each step: {"text": str, "item_id"?: str}. Accepts "title"/"step" as the text too."""
        out: List[Dict[str, Any]] = []
        for raw in steps:
            text = str(raw.get("text") or raw.get("title") or raw.get("step") or "").strip()
            if not text:
                continue
            step: Dict[str, Any] = {"text": text[:500]}
            if raw.get("item_id"):
                step["item_id"] = str(raw["item_id"])
            out.append(step)
        return out

    @staticmethod
    def _categorize(title: str) -> str:
        t = title.lower()
        if any(k in t for k in ("event", "planning", "conference")):
            return "event"
        if any(k in t for k in ("lab", "protocol", "experiment")):
            return "lab"
        if any(k in t for k in ("onboarding", "training", "new hire")):
            return "onboarding"
        return "general"
