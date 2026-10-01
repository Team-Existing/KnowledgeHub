"""
Architecture Decision Records in a local git checkout (Nygard / adr-tools and
MADR styles). Each ADR becomes one decision — no LLM involved — with its
status, context and consequences; "Superseded by" / "Supersedes" / "Amends"
lines become lineage edges between the ADRs. Dates and authors come from the
ADR itself, or else from the commit that added the file.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from starlette.concurrency import run_in_threadpool

from app.services.connectors.base import ConfigField, ConnectorError, FetchResult, Row, SourceDocument, checked_directory

KIND = "git_adr"
LABEL = "Git repository — Architecture Decision Records"
FILESYSTEM = True
FIELDS = [
    ConfigField("path", "Repository path", required=True, help="Absolute path of a local clone"),
    ConfigField("adr_dirs", "ADR folders", type="list",
                default=["docs/adr", "doc/adr", "docs/decisions", "docs/architecture/decisions", "adr", "decisions"],
                help="Relative to the repository root; the ones that exist are scanned"),
    ConfigField("web_url", "Web URL for links (optional)",
                help="e.g. https://github.com/acme/api/blob/main — used to link back to each ADR"),
    ConfigField("max_files", "Max ADRs per sync", type="number", default=500),
]

_SKIP_NAMES = {"readme.md", "index.md", "template.md", "adr-template.md", "toc.md", "_sidebar.md"}
_STATUS_WORDS = {
    "accepted": "active", "approved": "active", "active": "active", "done": "active",
    "proposed": "proposed", "draft": "proposed", "open": "proposed",
    "deprecated": "deprecated", "rejected": "rejected", "superseded": "superseded",
}
# relation phrase -> (kind, whether the *other* ADR is the newer one)
_RELATIONS = [
    (re.compile(r"superseded\s+by", re.I), "supersedes", True),
    (re.compile(r"\bsupersedes\b", re.I), "supersedes", False),
    (re.compile(r"amended\s+by", re.I), "amends", True),
    (re.compile(r"\bamends\b", re.I), "amends", False),
]
_REF = re.compile(r"\]\(\s*(?:\./)?([^)\s]+?\.md)\s*\)|\bADR[-\s#]*0*(\d+)\b|(?:^|\s)#?0*(\d+)\b", re.I)


def _number(name: str) -> Optional[int]:
    m = re.match(r"(?:adr[-_]?)?0*(\d+)", name, re.I)
    return int(m.group(1)) if m else None


def _sections(body: str) -> Dict[str, str]:
    """## heading -> text, with lower-cased headings."""
    out: Dict[str, str] = {}
    current: Optional[str] = None
    for line in body.splitlines():
        m = re.match(r"^#{2,3}\s+(.*)$", line)
        if m:
            current = m.group(1).strip().lower()
            out.setdefault(current, "")
        elif current:
            out[current] += line + "\n"
    return {k: v.strip() for k, v in out.items()}


def _section(sections: Dict[str, str], *names: str) -> str:
    """First section whose heading starts with one of `names`, trying names in order
    (so "decision outcome" wins over MADR's earlier "decision drivers")."""
    for name in names:
        for key, text in sections.items():
            if key.startswith(name) and not key.startswith("decision driver"):
                return text
    return ""


def _front_matter(text: str) -> Tuple[Dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip("'\"")
    return meta, text[end + 4:]


def _field_line(body: str, name: str) -> str:
    """'Date: 2024-01-02' or '* Status: accepted' style metadata lines."""
    m = re.search(rf"^\s*[*-]?\s*{name}\s*:\s*(.+)$", body, re.I | re.M)
    return m.group(1).strip() if m else ""


def parse_adr(rel_path: str, text: str) -> Optional[Row]:
    meta, body = _front_matter(text.replace("\r\n", "\n"))
    heading = re.search(r"^#\s+(.+)$", body, re.M)
    if not heading:
        return None
    title = re.sub(r"^(?:ADR[-\s]*)?\d+[.:)\s-]+\s*", "", heading.group(1).strip(), flags=re.I) or heading.group(1).strip()
    sections = _sections(body)

    status_text = meta.get("status") or _section(sections, "status") or _field_line(body, "status")
    first_word = (re.findall(r"[a-z]+", status_text.lower()) or [""])[0]
    relations: List[Row] = []
    for line in status_text.splitlines() or [status_text]:
        for pattern, kind, other_is_newer in _RELATIONS:
            m = pattern.search(line)
            if not m:
                continue
            for ref in _REF.finditer(line[m.end():]):
                target = ref.group(1) or ref.group(2) or ref.group(3)
                relations.append({"kind": kind, "target": target, "other_is_newer": other_is_newer})
            break

    date = meta.get("date") or _field_line(body, "date")
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", date)
    decision = _section(sections, "decision outcome", "decision") or _field_line(body, "decision")
    context = _section(sections, "context")
    consequences = _section(sections, "consequences", "positive consequences")
    return {
        "number": _number(Path(rel_path).name),
        "title": title,
        "status_word": first_word,
        "declared_status": _STATUS_WORDS.get(first_word),
        "date": date_match.group(0) if date_match else None,
        "decision": decision,
        "context": context,
        "consequences": consequences,
        "relations": relations,
        "deciders": meta.get("deciders") or _field_line(body, "deciders"),
    }


async def _git_first_commit(root: Path, rel_path: str) -> Tuple[Optional[str], Optional[str]]:
    """(ISO date, author) of the commit that added the file; (None, None) without git."""
    if not shutil.which("git"):
        return None, None
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(root), "log", "--diff-filter=A", "--follow", "--format=%aI|%an", "--", rel_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except (OSError, asyncio.TimeoutError):
        return None, None
    lines = [l for l in out.decode(errors="replace").splitlines() if "|" in l]
    if not lines:
        return None, None
    date, author = lines[-1].split("|", 1)
    return date.strip(), author.strip()


def _list_adrs(root: Path, dirs: List[str], limit: int) -> List[Path]:
    found: List[Path] = []
    for d in dirs:
        base = (root / d).resolve()
        if root not in base.parents and base != root:
            continue   # "../" in a configured folder must not escape the repository
        if base.is_dir():
            found += [p for p in sorted(base.rglob("*.md")) if p.name.lower() not in _SKIP_NAMES]
    unique = list(dict.fromkeys(found))
    return unique[:limit]


async def fetch(config: Row) -> FetchResult:
    root = checked_directory(config["path"])
    files = await run_in_threadpool(_list_adrs, root, config["adr_dirs"], config["max_files"])
    if not files:
        raise ConnectorError(f"No ADRs found under {', '.join(config['adr_dirs'])} in {root}")

    parsed: List[Tuple[str, Row, str]] = []
    errors: List[Row] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            text = await run_in_threadpool(path.read_text, "utf-8", "replace")
        except OSError as exc:
            errors.append({"external_id": rel, "error": str(exc)})
            continue
        adr = parse_adr(rel, text)
        if adr:
            parsed.append((rel, adr, text))

    by_number = {adr["number"]: rel for rel, adr, _ in parsed if adr["number"] is not None}
    by_name = {Path(rel).name.lower(): rel for rel, _, _ in parsed}

    def resolve(target: str) -> Optional[str]:
        if target.lower().endswith(".md"):
            return by_name.get(Path(target).name.lower())
        return by_number.get(int(target)) if target.isdigit() else None

    links: Dict[str, List[Row]] = {rel: [] for rel, _, _ in parsed}
    for rel, adr, _ in parsed:
        for r in adr["relations"]:
            other = resolve(r["target"])
            if not other or other == rel:
                continue
            newer, older = (other, rel) if r["other_is_newer"] else (rel, other)
            link = {"kind": r["kind"], "ref": older}
            if link not in links[newer]:
                links[newer].append(link)

    web = (config.get("web_url") or "").rstrip("/")
    docs: List[SourceDocument] = []
    for rel, adr, text in parsed:
        git_date, git_author = await _git_first_commit(root, rel)
        if adr["date"]:
            created = datetime.fromisoformat(adr["date"]).replace(tzinfo=timezone.utc).isoformat()
        else:
            created = git_date or datetime.fromtimestamp((root / rel).stat().st_mtime, tz=timezone.utc).isoformat()
        label = f"ADR-{adr['number']:04d}: {adr['title']}" if adr["number"] is not None else adr["title"]
        decision_text = adr["decision"] or adr["title"]
        declared = adr["declared_status"]
        if declared == "superseded" and any(l["kind"] == "supersedes" for d in links.values() for l in d
                                            if l["ref"] == rel):
            declared = None   # the lineage edge already says so, and names the successor
        details = {
            "what": decision_text[:1500],
            "why": adr["context"][:1500],
            "consequences": adr["consequences"][:1500],
            "adr_number": adr["number"],
            "adr_status": adr["status_word"],
            "who": adr["deciders"] or git_author or "",
            "evidence": decision_text[:500],
            "confidence": 1.0,
            "extractor": "adr",
        }
        docs.append(SourceDocument(
            external_id=rel,
            title=label,
            content=text,
            mode="structured",
            source_type="adr",
            url=f"{web}/{rel}" if web else (root / rel).as_uri(),
            author=git_author or "unknown",
            created_at=created,
            tags=["adr"],
            entries=[{
                "prefix": "adr", "type": "decision", "title": label,
                "details": {k: v for k, v in details.items() if v not in ("", None)},
                # an ADR is already a reviewed, recorded decision
                "review_status": "accepted",
                "declared_status": declared,
            }],
            links=links[rel],
        ))
    return FetchResult(docs, errors)
