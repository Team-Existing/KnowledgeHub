"""
Every backend feature has a frontend, and every frontend call has a backend.

Reads the Angular sources (frontend/src/app) for API calls and matches them
against FastAPI's route table, both ways:
  - an endpoint nothing in the UI calls is a feature users can't reach
  - a UI call with no matching route is a broken screen
No database needed. Skipped if the frontend isn't checked out next to the backend.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Set, Tuple

import pytest

from app.main import app

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "app"

# Endpoints that are deliberately not called from the UI, with the reason.
NOT_FOR_THE_UI: Set[Tuple[str, str]] = set()

_HTTP_CALL = re.compile(
    r"http\.(get|post|put|patch|delete|request)\s*(?:<(?:[^<>]|<[^<>]*>)*>)?\s*\(\s*(?:'([A-Z]+)'\s*,\s*)?"
    r"`\$\{API_BASE\}((?:[^`$]|\$\{(?:[^{}]|\{[^{}]*\})*\})*)`", re.S)
_ANY_URL = re.compile(r"`\$\{API_BASE\}((?:[^`$]|\$\{(?:[^{}]|\{[^{}]*\})*\})*)`", re.S)
_TERNARY = re.compile(r"\$\{[^}]*\?\s*'([^']*)'\s*:\s*'([^']*)'\s*\}")


def _expand(raw: str) -> List[str]:
    """Template literal -> concrete path shapes: ${cond ? 'a' : 'b'} gives both, other ${...} become X."""
    m = _TERNARY.search(raw)
    variants = [raw.replace(m.group(0), m.group(1)), raw.replace(m.group(0), m.group(2))] if m else [raw]
    out = []
    for v in variants:
        path, i = [], 0
        while i < len(v):
            if v.startswith("${", i):
                depth, j = 1, i + 2
                while j < len(v) and depth:
                    depth += {"{": 1, "}": -1}.get(v[j], 0)
                    j += 1
                path.append("X")
                i = j
            else:
                path.append(v[i])
                i += 1
        shape = re.sub(r"X+", "X", "".join(path)).split("?")[0]
        out.append(re.sub(r"X$", "", shape) if shape.endswith("X") and not shape.endswith("/X") else shape)
    return out


def _frontend_calls() -> List[Tuple[str, str, str]]:
    """(METHOD or '*', path shape, file). '*' = a URL built outside HttpClient (e.g. fetch for streaming)."""
    calls = []
    for ts in FRONTEND.rglob("*.ts"):
        if ts.name.endswith(".spec.ts"):
            continue
        text = ts.read_text(encoding="utf-8")
        spans = []
        for m in _HTTP_CALL.finditer(text):
            spans.append(m.span(3))
            method = (m.group(2) or m.group(1)).upper()
            calls += [(method, p, ts.name) for p in _expand(m.group(3))]
        for m in _ANY_URL.finditer(text):
            if not any(a <= m.start(1) < b for a, b in spans):
                calls += [("*", p, ts.name) for p in _expand(m.group(1))]
    return calls


def _routes() -> List[Tuple[str, "re.Pattern[str]", str]]:
    # from the OpenAPI schema: FastAPI includes routers lazily, so app.routes doesn't
    # list their endpoints (no route here sets include_in_schema=False)
    out = []
    for path, operations in app.openapi()["paths"].items():
        rx = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")
        out += [(m.upper(), rx, path) for m in operations if m.upper() not in ("HEAD", "OPTIONS")]
    return out


pytestmark = pytest.mark.skipif(not FRONTEND.is_dir(), reason="frontend sources not found next to the backend")


def test_every_frontend_call_has_a_backend_route():
    routes = _routes()
    missing = [f"{f}: {m} {p}" for m, p, f in _frontend_calls()
               if not any((m in ("*", rm)) and rx.match(p) for rm, rx, _ in routes)]
    assert not missing, "frontend calls with no backend route:\n" + "\n".join(missing)


def test_every_backend_endpoint_is_reachable_from_the_frontend():
    calls = _frontend_calls()
    unused = [f"{m} {path}" for m, rx, path in _routes()
              if (m, path) not in NOT_FOR_THE_UI
              and not any(cm in ("*", m) and rx.match(p) for cm, p, _ in calls)]
    assert not unused, "backend endpoints with no frontend caller:\n" + "\n".join(sorted(unused))
