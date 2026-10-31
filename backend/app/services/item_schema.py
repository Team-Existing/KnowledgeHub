"""
Shared normalization for extracted knowledge items.

Both knowledge_extraction.py (regex) and llm_extraction.py (LLM) produce
raw dicts with different field names. This module is the single convergence
point — every item passes through normalize_item_details() before being
persisted, guaranteeing a stable downstream shape.

Canonical details schema
------------------------
{
    "what":       str   # primary content / title restatement
    "why":        str   # rationale or context
    "who":        str   # person / owner / decision-maker
    "severity":   str   # "low" | "medium" | "high"  (risks only)
    "steps":      list  # ordered steps (how-to only)
    "evidence":   str   # source sentence(s)
    "confidence": float # 0.0 – 1.0, same scale for both extractors
    "extractor":  str   # "regex" | "llm" | "okf"
}

Fields not relevant to a given type are omitted (not set to null/empty).
"""
from __future__ import annotations

from typing import Any, Dict, List


def normalize_item_details(
    raw: Dict[str, Any],
    item_type: str,
    extractor: str,
) -> Dict[str, Any]:
    """
    Produce a canonical details dict from a raw extractor output.

    Parameters
    ----------
    raw:        the dict produced by the extractor
    item_type:  "decision" | "risk" | "lesson" | "how-to" | "best-practice" |
                "checklist" | "action-item" | "knowledge-item"
    extractor:  "regex" | "llm" | "okf"
    """
    out: Dict[str, Any] = {"extractor": extractor}

    # ── primary content ────────────────────────────────────────────────────
    what = (
        raw.get("what")
        or raw.get("task")       # action-item from LLM
        or raw.get("lesson")     # lesson from regex
        or raw.get("risk")       # risk from regex
        or raw.get("pattern")    # how-to from regex
        or raw.get("practice")   # best-practice from regex
        or raw.get("item")       # checklist from regex
        or ""
    )
    if what:
        out["what"] = str(what).strip()

    # ── rationale / context ────────────────────────────────────────────────
    why = raw.get("why") or raw.get("context") or raw.get("rationale") or ""
    if why:
        out["why"] = str(why).strip()

    # ── owner / actor ──────────────────────────────────────────────────────
    who = raw.get("who") or raw.get("owner") or raw.get("author") or ""
    if who:
        out["who"] = str(who).strip()

    # ── due date (action-items) ────────────────────────────────────────────
    due = raw.get("due") or raw.get("when") or ""
    if due:
        out["due"] = str(due).strip()

    # ── alternatives (decisions): the options considered and not chosen ───
    if item_type == "decision":
        alternatives = clean_alternatives(raw.get("alternatives"))
        if alternatives:
            out["alternatives"] = alternatives

    # ── severity (risks) ──────────────────────────────────────────────────
    if item_type == "risk":
        severity = str(raw.get("severity") or "low").lower()
        out["severity"] = severity if severity in ("low", "medium", "high") else "low"

    # ── steps (how-to) ────────────────────────────────────────────────────
    if item_type == "how-to":
        steps = raw.get("steps")
        if isinstance(steps, list) and steps:
            out["steps"] = [str(s) for s in steps]

    # ── evidence ──────────────────────────────────────────────────────────
    evidence = raw.get("evidence") or ""
    if evidence:
        out["evidence"] = str(evidence).strip()

    # ── confidence — unified 0.0–1.0 scale ────────────────────────────────
    raw_conf = raw.get("confidence")
    if raw_conf is not None:
        try:
            conf = float(raw_conf)
            # LLM path has no confidence field — we assign a fixed prior
            # Regex path produces 0.55–0.95 already on the right scale
            out["confidence"] = round(min(max(conf, 0.0), 1.0), 3)
        except (TypeError, ValueError):
            pass

    if "confidence" not in out:
        # LLM output: assign a fixed prior (LLM is generally more precise
        # than regex but we have no per-item score)
        out["confidence"] = 0.75 if extractor == "llm" else 0.60

    return out


# ── Confidence calibration for the regex extractor ────────────────────────
#
# Original formula had two problems:
#   1. No length normalisation — a 40-word sentence with 1 keyword scored
#      the same as a 6-word sentence with 1 keyword.
#   2. The detail_bonus (len >= 8 words) rewarded verbosity, not precision.
#
# New formula:
#   base        = 0.50
#   keyword hit = +0.12 per unique keyword match (capped at 3 hits → +0.36)
#   rationale   = +0.10 if causal connective present
#   density     = keyword_hits / word_count, bonus up to +0.05
#   cap         = 0.92  (leave headroom for human review to push to 1.0)

def calibrated_confidence(
    sentence: str,
    keywords: tuple,
    has_rationale: bool = False,
) -> float:
    import re
    lowered = sentence.lower()
    words = lowered.split()
    word_count = max(len(words), 1)

    keyword_hits = sum(
        1 for kw in keywords
        if re.search(rf"\b{re.escape(kw)}\b", lowered)
    )

    density_bonus = round(min(keyword_hits / word_count, 0.05), 3)
    score = (
        0.50
        + min(keyword_hits, 3) * 0.12
        + (0.10 if has_rationale else 0.0)
        + density_bonus
    )
    return round(min(score, 0.92), 3)


def clean_alternatives(value: Any) -> List[str]:
    """The options a decision considered and didn't choose, as a short list: from a list, or "A, B or C"."""
    import re
    if isinstance(value, str):
        value = re.split(r",|;|\bor\b|\band\b", value)
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for v in value:
        text = str(v or "").strip(" .;:-")
        if 1 < len(text) <= 80 and text.lower() not in ("none", "n/a", "") and text not in out:
            out.append(text)
    return out[:6]

