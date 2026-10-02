"""
Local folder: meeting transcripts (.vtt / .srt caption exports, or .txt),
notes and documents (.md, .txt, .pdf, .docx). Nothing leaves the machine.
"""
from __future__ import annotations

import fnmatch
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.services.connectors.base import (
    ConfigField, ConnectorError, FetchResult, Row, SourceDocument, checked_directory, walk_files,
)
from app.services.connectors.parsers import captions_to_transcript
from app.services.file_ingestion import MAX_UPLOAD_BYTES, _extract_docx, _extract_pdf

KIND = "folder"
LABEL = "Local folder (notes, docs, meeting transcripts)"
FILESYSTEM = True
FIELDS = [
    ConfigField("path", "Folder path", required=True, help="Absolute path, e.g. C:\\Users\\me\\Meetings"),
    ConfigField("patterns", "File patterns", type="list",
                default=["*.md", "*.txt", "*.vtt", "*.srt", "*.pdf", "*.docx"],
                help="Matched against file names, in all subfolders"),
    ConfigField("transcript_patterns", "Treat as transcripts", type="list", default=["*.vtt", "*.srt"],
                help="These files go through meeting-transcript extraction; add e.g. *transcript*.txt"),
    ConfigField("max_files", "Max files per sync", type="number", default=200),
]

_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".obsidian", ".trash"}


def _matches(name: str, patterns: List[str]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, p.lower()) for p in patterns)


def _list_files(root: Path, patterns: List[str], limit: int) -> List[Path]:
    found: List[Path] = []
    for path in walk_files(root, _SKIP_DIRS):
        if _matches(path.name, patterns):
            found.append(path)
            if len(found) >= limit:
                break
    return found


def _read(path: Path) -> str:
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ConnectorError(f"larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            with path.open("rb") as fh:
                return _extract_pdf(fh)
        if suffix == ".docx":
            with path.open("rb") as fh:
                return _extract_docx(fh)
    except HTTPException as exc:   # the upload parsers report failures as HTTP errors
        raise ConnectorError(str(exc.detail))
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix in (".vtt", ".srt"):
        return captions_to_transcript(text)
    return text


async def fetch(config: Row) -> FetchResult:
    root = checked_directory(config["path"])
    files = await run_in_threadpool(_list_files, root, config["patterns"], config["max_files"])
    docs: List[SourceDocument] = []
    errors: List[Row] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        as_transcript = _matches(path.name, config["transcript_patterns"])
        try:
            content = (await run_in_threadpool(_read, path)).strip()
        except (ConnectorError, OSError) as exc:
            errors.append({"external_id": rel, "error": str(exc)})
            continue
        if not content:
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        docs.append(SourceDocument(
            external_id=rel,
            title=path.stem.replace("_", " ").replace("-", " ").strip() or path.name,
            content=content,
            mode="transcript" if as_transcript else "document",
            source_type="transcript" if as_transcript else "file",
            url=path.as_uri(),
            created_at=modified,
            tags=[p for p in path.relative_to(root).parts[:-1]][:3],
        ))
    return FetchResult(docs, errors)
