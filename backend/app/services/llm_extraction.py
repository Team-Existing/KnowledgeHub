from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from app.services.item_schema import normalize_item_details
from app.services import llm_client
from app.services.knowledge_extraction import KnowledgeExtraction

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

_DOCUMENT_SYSTEM = """\
You are a knowledge analyst. Extract reusable knowledge from the document excerpt below.

Only extract items someone would deliberately look up later. Ordinary narrative,
background, examples and definitions are NOT items. Prefer a few precise items
over many vague ones. If the excerpt contains nothing worth keeping, return all
empty arrays.

OUTPUT RULES — follow exactly:
1. Return ONLY a single JSON object. No markdown, no code fences, no explanation.
2. Use this exact shape:
{
  "decisions": [{"what": "...", "why": "...", "who": "..."}],
  "action_items": [{"task": "...", "owner": "...", "due": "..."}],
  "how_tos": [{"what": "...", "steps": ["...", "..."]}],
  "best_practices": [{"what": "...", "why": "..."}],
  "lessons": [{"what": "...", "why": "..."}],
  "risks": [{"risk": "...", "severity": "low|medium|high"}]
}
3. decisions: choices the text states were made or agreed — not proposals or options still being considered.
4. action_items: concrete tasks assigned to someone or explicitly still to be done.
5. how_tos: procedures with at least two ordered steps. "what" names the goal; steps are short imperative phrases.
6. best_practices: explicit recommendations or rules the text tells the reader to follow.
7. lessons: things the author says were learned from experience, incidents or mistakes.
8. risks: concrete threats, blockers or failure modes — not every mention of a "problem" or "issue".
9. Write each "what" / "task" / "risk" as one self-contained sentence of at most 25 words.
10. At most 5 items per category.
11. Use "" for any unknown field. Never use null.
12. Do NOT wrap the JSON in ```json ... ``` or any other wrapper.\
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
    # normalize so fallback action items ("task") get the canonical "what" key
    return {**_normalize_llm_result(_fallback(text)), "llm_error": "no LLM reachable – regex fallback used"}


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


_TRANSCRIPT_KINDS = {"decisions": "decision", "action_items": "action-item", "risks": "risk"}

# result key → item type, for everything extract_from_document returns
DOCUMENT_KINDS = {
    **_TRANSCRIPT_KINDS,
    "how_tos": "how-to",
    "best_practices": "best-practice",
    "lessons": "lesson",
}


def _normalize_llm_result(
    result: Dict[str, Any], kinds: Dict[str, str] = _TRANSCRIPT_KINDS
) -> Dict[str, Any]:
    for key, item_type in kinds.items():
        result[key] = [
            normalize_item_details(entry, item_type, "llm")
            for entry in result.get(key) or []
            if isinstance(entry, dict)
        ]
    return result


# ---------------------------------------------------------------------------
# Documents (file uploads, URLs)
# ---------------------------------------------------------------------------

# ~1.5k tokens per chunk leaves room for the prompt and a 1.5k-token reply
# inside an 8k context window.
MAX_CHUNK_CHARS = 6000
# Bounds latency on a local model; anything past this is not extracted.
MAX_CHUNKS = 8


def _split_long(paragraph: str, max_chars: int) -> List[str]:
    """Cut an oversized paragraph at the last sentence end before the limit."""
    pieces: List[str] = []
    while len(paragraph) > max_chars:
        cut = paragraph.rfind(". ", 0, max_chars)
        cut = cut + 1 if cut >= max_chars // 2 else max_chars
        pieces.append(paragraph[:cut].strip())
        paragraph = paragraph[cut:].strip()
    if paragraph:
        pieces.append(paragraph)
    return pieces


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    """Pack paragraphs into chunks of at most max_chars."""
    chunks: List[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        for piece in _split_long(paragraph.strip(), max_chars):
            if current and len(current) + 2 + len(piece) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _regex_chunk_result(chunk: str) -> Dict[str, List[Dict[str, Any]]]:
    """Regex extraction for one chunk, in the same shape as an LLM result."""
    rx = KnowledgeExtraction()
    return {
        "decisions": rx.extract_decisions(chunk),
        "action_items": [],
        "how_tos": rx.mine_how_to_patterns(chunk),
        "best_practices": [
            normalize_item_details({"practice": p}, "best-practice", "regex")
            for p in rx.identify_best_practices(chunk)
        ],
        "lessons": rx.extract_lessons_learned(chunk),
        "risks": rx.recognize_risk_patterns(chunk),
    }


def _dedupe(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for entry in entries:
        key = re.sub(r"\W+", " ", entry.get("what", "")).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(entry)
    return out


async def extract_from_document(text: str, workspace: Optional[Any] = None) -> Dict[str, Any]:
    """
    LLM extraction for long-form documents, chunk by chunk.

    Returns {"llm_unavailable": True, "llm_error": ...} when the LLM does not
    answer the first chunk — the caller then falls back to regex for the whole
    document. A later chunk whose reply is missing or unparseable falls back to
    regex for that chunk only. Every entry's details["extractor"] records which
    extractor produced it.
    """
    all_chunks = chunk_text(text)
    chunks = all_chunks[:MAX_CHUNKS]
    result: Dict[str, Any] = {key: [] for key in DOCUMENT_KINDS}
    failed: List[str] = []

    for index, chunk in enumerate(chunks):
        content = await llm_client.chat(
            messages=[
                {"role": "system", "content": _DOCUMENT_SYSTEM},
                {"role": "user", "content": chunk},
            ],
            temperature=0,
            max_tokens=1500,
            json_mode=True,
            workspace=workspace,
        )
        if content is None and index == 0:
            return {"llm_unavailable": True, "llm_error": "no LLM reachable – regex fallback used"}
        try:
            if content is None:
                raise ValueError("no response")
            parsed = _normalize_llm_result(_extract_json(content), DOCUMENT_KINDS)
        except Exception as exc:
            failed.append(f"chunk {index + 1}: {exc}")
            parsed = _regex_chunk_result(chunk)
        for key in DOCUMENT_KINDS:
            result[key].extend(parsed[key])

    for key in DOCUMENT_KINDS:
        result[key] = _dedupe(result[key])
    result["extraction"] = {
        "chunks": len(chunks),
        "chunks_regex_fallback": len(failed),
        "chars_not_extracted": sum(len(c) for c in all_chunks[MAX_CHUNKS:]),
    }
    if failed:
        result["llm_error"] = "; ".join(failed)
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
