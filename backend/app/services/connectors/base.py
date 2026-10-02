"""
Shared pieces for connectors: the document model every connector produces,
config field descriptions (which also drive the frontend form), validation,
and the HTTP client factory (tests swap it for a mock transport).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional, Set

import httpx

Row = Dict[str, Any]

# what a sync does with a document: LLM extraction for prose, transcript
# extraction for conversations, or items the source states outright (ADRs)
IngestMode = Literal["document", "transcript", "structured"]


class ConnectorError(Exception):
    """Bad config or an unreachable/refusing source; shown to the user as-is."""


@dataclass
class SourceDocument:
    external_id: str                 # stable id within the source (path, issue key, PR number)
    title: str
    content: str
    mode: IngestMode = "document"
    source_type: str = "document"
    url: str = ""                    # link back to the original, when there is one
    author: str = "unknown"
    created_at: Optional[str] = None  # when the source says this happened (ISO 8601)
    tags: List[str] = field(default_factory=list)
    # structured mode: [{prefix, type, title, details, review_status?, declared_status?}]
    entries: List[Row] = field(default_factory=list)
    # lineage between documents of this source: [{"kind": "supersedes", "ref": <external_id>}]
    # meaning "this document's decision <kind> the decision in <ref>"
    links: List[Row] = field(default_factory=list)


@dataclass
class FetchResult:
    documents: List[SourceDocument]
    errors: List[Row] = field(default_factory=list)   # [{"external_id", "error"}] for items that failed to read


@dataclass
class ConfigField:
    name: str
    label: str
    type: Literal["text", "password", "number", "list"] = "text"
    required: bool = False
    secret: bool = False
    default: Any = None
    help: str = ""

    def describe(self) -> Row:
        return {"name": self.name, "label": self.label, "type": self.type, "required": self.required,
                "secret": self.secret, "default": self.default, "help": self.help}


def validate_config(fields: List[ConfigField], raw: Row) -> Row:
    """Keep known fields only, apply defaults, coerce types, enforce required ones."""
    out: Row = {}
    for f in fields:
        value = raw.get(f.name)
        if value in (None, "", []):
            value = f.default
        if value in (None, "", []):
            if f.required:
                raise ConnectorError(f"{f.label} is required")
            continue
        if f.type == "number":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ConnectorError(f"{f.label} must be a whole number")
            if value < 1:
                raise ConnectorError(f"{f.label} must be at least 1")
        elif f.type == "list":
            if isinstance(value, str):
                value = [v.strip() for v in value.replace("\n", ",").split(",")]
            value = [str(v).strip() for v in value if str(v).strip()]
        else:
            value = str(value).strip()
        out[f.name] = value
    return out


def user_url(raw: str, label: str) -> httpx.URL:
    """A URL the user typed into the connector: http(s) with a host, nothing else."""
    try:
        url = httpx.URL(str(raw).strip())
    except Exception:
        raise ConnectorError(f"{label} is not a valid URL")
    if url.scheme not in ("http", "https") or not url.host:
        raise ConnectorError(f"{label} must be a full http(s) address, e.g. https://example.com")
    return url


def host_guard(allowed_hosts: Set[str]):
    """
    Request hook: refuse any request whose host isn't one the user gave this
    connector — including redirects and URLs that come back inside API
    responses (e.g. a "comments_url" pointing somewhere else).
    """
    allowed = {h.lower() for h in allowed_hosts}

    async def check(request: httpx.Request) -> None:
        if request.url.host.lower() not in allowed:
            raise ConnectorError(
                f"Refused to contact {request.url.host}: this connector may only reach {', '.join(sorted(allowed))}"
            )
    return check


def make_client(allowed_hosts: Set[str], transport: Optional[httpx.AsyncBaseTransport] = None,
                **kwargs: Any) -> httpx.AsyncClient:
    """
    Every connector HTTP call goes through here, confined to `allowed_hosts`
    (derived from the addresses in the connector's own config). Connectors have
    no built-in endpoints, so nothing is contacted that a user didn't provide.
    Tests pass a MockTransport; everything else (redirects, the guard) is the same.
    """
    return httpx.AsyncClient(timeout=30, follow_redirects=True, transport=transport,
                             event_hooks={"request": [host_guard(allowed_hosts)]}, **kwargs)


async def get_json(client: httpx.AsyncClient, method: str, url: str, what: str, **kwargs: Any) -> Any:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"Could not reach {what}: {exc}")
    if response.status_code in (401, 403):
        raise ConnectorError(f"{what} refused the credentials ({response.status_code})")
    if response.status_code >= 400:
        raise ConnectorError(f"{what} answered {response.status_code}: {response.text[:300]}")
    try:
        return response.json()
    except ValueError:
        raise ConnectorError(f"{what} did not return JSON")


def allowed_roots() -> List[Path]:
    """
    CONNECTOR_ROOTS (os.pathsep-separated: ';' on Windows, ':' elsewhere) lists the
    only directories filesystem connectors may read. Unset = filesystem
    connectors are disabled entirely (fail closed).
    """
    raw = os.getenv("CONNECTOR_ROOTS", "")
    return [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]


def filesystem_disabled_reason() -> Optional[str]:
    if not allowed_roots():
        return "Folder connectors are disabled on this server until an administrator sets CONNECTOR_ROOTS"
    return None


def is_within(path: Path, root: Path) -> bool:
    """Whether `path`, after resolving symlinks and '..', is `root` or inside it."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):   # broken or looping symlink
        return False
    return resolved == root or root in resolved.parents


def checked_directory(raw_path: str) -> Path:
    reason = filesystem_disabled_reason()
    if reason:
        raise ConnectorError(reason)
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise ConnectorError("Path must be absolute")
    path = path.resolve()
    if not any(is_within(path, r) for r in allowed_roots()):
        raise ConnectorError("Path is outside the directories allowed by CONNECTOR_ROOTS")
    if not path.is_dir():
        raise ConnectorError(f"Directory not found: {path}")
    return path


def walk_files(root: Path, skip_dirs: Set[str] = frozenset()) -> Iterator[Path]:
    """
    Files under `root`, sorted, without following directory symlinks; file
    symlinks pointing outside `root` are skipped, so a link can't pull in
    files from outside the allowed directories.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if is_within(path, root) and path.is_file():
                yield path
