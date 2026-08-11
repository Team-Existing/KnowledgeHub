from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from app.services.item_schema import normalize_item_details
from app.services import llm_client

# Prompts are written to be explicit and unambiguous for local models
# (llama3.1:8b class) which need more formatting guidance than GPT-4-class models.

_EXTRACTION_SYSTEM = """\
You are a knowledge analyst. Extract structured knowledge from the text below.
The text may be a meeting transcript, an email thread, or a Slack conversation.

OUTPUT RULES — follow exactly:
1. Return ONLY a single JSON object. No markdown, no code fences, no explanation.
2. Use this exact shape:
{
  "decisions": [{"what": "...", "why": "...", "who": "..."}],
  "action_items": [{"task": "...", "owner": "...", "due": "..."}],
  "risks": [{"risk": "...", "severity": "low|medium|high"}],
  "summary": "..."
}
3. decisions: things explicitly agreed, decided, or confirmed — in any format.
4. action_items: concrete tasks assigned or volunteered by a person.
5. risks: concerns, blockers, open questions, or uncertainties raised.
6. summary: 2-3 sentences covering the main outcome or thread conclusion.
7. Use "" for any unknown field. Never use null.
8. If nothing fits a category, use an empty array [].
9. Do NOT wrap the JSON in ```json ... ``` or any other wrapper.\
"""

_SUMMARY_SYSTEM = """\
Summarise the document below in 2-3 sentences.
Return ONLY the summary text. No labels, no JSON, no markdown.\
"""


def _extract_json(raw: str) -> Dict[str, Any]:
    """
    Parse JSON from LLM output, tolerating accidental markdown fences
    that some local models emit despite instructions.
    """
    # strip ```json ... ``` or ``` ... ``` wrappers
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    return json.loads(cleaned)


def _preprocess(text: str, source_type: str = "transcript") -> str:
    """
    Normalise source-specific formatting before the LLM sees the content.
    - email: strip quoted reply chains (lines starting with >) and common headers
    - slack: convert "Username [timestamp]:" lines to "Username: "
    - transcript: pass through unchanged
    """
    if source_type == "email":
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            # drop quoted reply lines and common email header prefixes
            if stripped.startswith(">") or re.match(r"^(From|To|Cc|Bcc|Date|Subject|Sent):?\ ", stripped, re.IGNORECASE):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    if source_type == "slack":
        # "Username  [10:32 AM]" → "Username:"
        text = re.sub(r"^(.+?)\s+\[\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AP]M)?\]\s*", r"\1: ", text, flags=re.MULTILINE)
        return text.strip()

    return text.strip()


async def extract_from_transcript(text: str, source_type: str = "transcript", workspace: Optional[Any] = None) -> Dict[str, Any]:
    cleaned = _preprocess(text, source_type)
    content = await llm_client.chat(
        messages=[
            {"role": "system", "content": _EXTRACTION_SYSTEM},
            {"role": "user", "content": cleaned[:12000]},
        ],
        temperature=0,
        max_tokens=1500,
        json_mode=True,
        workspace=workspace,
    )
    if content:
        try:
            return _normalize_llm_result(_extract_json(content))
        except Exception as exc:
            return {**_normalize_llm_result(_fallback(text)), "llm_error": str(exc)}
    return {**_fallback(text), "llm_error": "no LLM reachable – regex fallback used"}


def _fallback(text: str) -> Dict[str, Any]:
    """Regex fallback when no LLM is reachable."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 10]
    decisions = [{"what": s, "why": "", "who": ""} for s in sentences
                 if any(k in s.lower() for k in ("decided", "agreed", "approved", "we will"))]
    actions = [{"task": s, "owner": "", "due": ""} for s in sentences
               if any(k in s.lower() for k in ("action:", "todo:", "will ", "should ", "needs to", "follow up"))]
    return {
        "decisions": decisions[:10],
        "action_items": actions[:10],
        "risks": [],
        "summary": sentences[0] if sentences else "",
        "llm_error": "no LLM reachable – regex fallback used",
    }


def _normalize_llm_result(result: Dict[str, Any]) -> Dict[str, Any]:
    result["decisions"] = [
        normalize_item_details(d, "decision", "llm") for d in result.get("decisions") or []
    ]
    result["action_items"] = [
        normalize_item_details(a, "action-item", "llm") for a in result.get("action_items") or []
    ]
    result["risks"] = [
        normalize_item_details(r, "risk", "llm") for r in result.get("risks") or []
    ]
    return result


async def _summarise_text(text: str, workspace: Optional[Any] = None) -> str:
    """Return a 2-3 sentence condensed summary for the summary index."""
    content = await llm_client.chat(
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": text[:8000]},
        ],
        temperature=0,
        max_tokens=150,
        workspace=workspace,
    )
    return content if content else text.strip()[:300]
