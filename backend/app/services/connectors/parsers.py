"""Text extraction for formats connectors meet: caption files and Atlassian Document Format."""
from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

_TIMESTAMP = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}\s*-->\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}")
_VOICE = re.compile(r"<v(?:\.[\w.-]+)?\s+([^>]+)>(.*?)(?:</v>|$)", re.DOTALL)
_TAG = re.compile(r"</?[^>]+>")
_SPEAKER_PREFIX = re.compile(r"^([A-Z][\w .'-]{0,40}):\s+(.*)$")


def captions_to_transcript(text: str) -> str:
    """
    WebVTT / SRT captions (Zoom, Teams, Meet recordings) -> "Speaker: text"
    lines. Timing, cue numbers, headers and NOTE/STYLE blocks are dropped, and
    consecutive cues from the same speaker are merged into one turn.
    """
    turns: List[Tuple[Optional[str], str]] = []
    skipping_block = False
    for raw in text.replace("\r\n", "\n").replace("﻿", "").split("\n"):
        line = raw.strip()
        if not line:
            skipping_block = False
            continue
        if skipping_block:
            continue
        if line.startswith("WEBVTT") or line.startswith(("NOTE", "STYLE", "REGION")):
            skipping_block = not line.startswith("WEBVTT")
            continue
        if line.isdigit() or _TIMESTAMP.match(line) or "-->" in line:
            continue
        speaker: Optional[str] = None
        voice = _VOICE.search(line)
        if voice:
            speaker, line = voice.group(1).strip(), voice.group(2)
        line = _TAG.sub("", line).strip()
        if not voice:
            prefixed = _SPEAKER_PREFIX.match(line)
            if prefixed:
                speaker, line = prefixed.group(1).strip(), prefixed.group(2)
        if not line:
            continue
        if turns and turns[-1][0] == speaker:
            turns[-1] = (speaker, f"{turns[-1][1]} {line}")
        else:
            turns.append((speaker, line))
    return "\n".join(f"{s}: {t}" if s else t for s, t in turns)


def adf_to_text(node: Any) -> str:
    """Atlassian Document Format (Jira Cloud rich text) -> plain text with paragraphs and list bullets."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node   # Jira Server / API v2 sends plain wiki text
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji"):
        return (node.get("attrs") or {}).get("text", "")
    if kind == "inlineCard":
        return (node.get("attrs") or {}).get("url", "")
    inner = adf_to_text(node.get("content") or [])
    if kind == "listItem":
        return f"- {inner.strip()}\n"
    if kind in ("paragraph", "heading", "blockquote", "codeBlock", "panel", "rule"):
        return f"{inner.strip()}\n\n"
    if kind in ("bulletList", "orderedList", "table"):
        return f"{inner}\n"
    if kind in ("tableRow",):
        return inner.replace("\n\n", " | ").strip(" |") + "\n"
    return inner
