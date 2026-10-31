"""
Decision Copilot for GitHub pull requests: the decisions a PR touches,
posted where the change is being made. Advisory only: it writes one comment
and never approves, requests changes, labels, merges or edits anything.

A PR's title, description and changed file paths go through impact analysis
(services/impact.py). The comment is posted only when the record has
something to say:
  - a current decision the change conflicts with or changes (the model's call), or
  - knowledge tied to a system the PR touches (linked or named), unless the
    model judged it unrelated.
Each PR gets one comment, found again by a hidden marker and updated when the
PR changes; a commit that was already analysed isn't analysed twice.

It runs from a GitHub connector (its repository link and token; the token
needs permission to write pull request comments) either on demand from the
app or from GitHub's pull_request webhook, signed with the connector's secret.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app import coordination, crypto
from app.arcadedb import ArcadeClient, ArcadeSession
from app.repositories import ConnectorRepository, UserRepository
from app.services import impact
from app.services.connectors import REGISTRY, base, github
from app.services.connectors.base import ConnectorError
from app.services.connectors.sync import decrypted_config, secret_context
from app.services.providers import use_model

logger = logging.getLogger(__name__)

Row = Dict[str, Any]

MARKER = "<!-- knowledge-hubs-copilot -->"
PR_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review", "edited"}
MAX_FILES = 100
MAX_BODY = 3000
_LISTED = 5           # entries per section in a comment


class CopilotError(Exception):
    """Shown to the user (or logged for webhook runs)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── settings on the connector ───────────────────────────────────────────────

def settings(connector: Row) -> Row:
    s = connector.get("copilot") or {}
    return {"enabled": bool(s.get("enabled")), "has_secret": bool(s.get("webhook_secret"))}


def new_secret(connector_id: str) -> Tuple[str, str]:
    """(plain secret to show once, encrypted value to store)"""
    plain = secrets.token_urlsafe(32)
    return plain, crypto.encrypt(plain, secret_context(connector_id) + ":copilot")


def verify_signature(connector: Row, body: bytes, signature: Optional[str]) -> bool:
    """GitHub's X-Hub-Signature-256: sha256=HMAC(secret, raw body)."""
    stored = (connector.get("copilot") or {}).get("webhook_secret")
    if not stored or not signature or not signature.startswith("sha256="):
        return False
    try:
        secret = crypto.decrypt(stored, secret_context(connector["id"]) + ":copilot")
    except Exception:
        logger.warning("Copilot: the webhook secret of connector %s can't be decrypted", connector["id"])
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── GitHub ──────────────────────────────────────────────────────────────────

def _client(config: Row):
    repo, api, host = github.resolve(config)
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if config.get("token"):
        headers["Authorization"] = f"Bearer {config['token']}"
    return repo, api, base.make_client({host}, headers=headers)


async def fetch_pr(config: Row, number: int) -> Row:
    repo, api, client = _client(config)
    async with client:
        pr = await base.get_json(client, "GET", f"{api}/repos/{repo}/pulls/{number}", "GitHub")
        files = await base.get_json(client, "GET", f"{api}/repos/{repo}/pulls/{number}/files", "GitHub",
                                    params={"per_page": MAX_FILES})
    return {"number": number, "title": pr.get("title") or "", "body": (pr.get("body") or "")[:MAX_BODY],
            "url": pr.get("html_url"), "head_sha": (pr.get("head") or {}).get("sha"),
            "draft": bool(pr.get("draft")), "state": pr.get("state"),
            "files": [f.get("filename") for f in files if f.get("filename")][:MAX_FILES]}


async def upsert_comment(config: Row, number: int, body: str) -> Tuple[str, str, bool]:
    """Post the comment, or edit ours if the PR already has one. (id, url, updated)"""
    repo, api, client = _client(config)
    async with client:
        existing = None
        for page in range(1, 6):
            comments = await base.get_json(client, "GET", f"{api}/repos/{repo}/issues/{number}/comments", "GitHub",
                                           params={"per_page": 100, "page": page})
            existing = next((c for c in comments if MARKER in (c.get("body") or "")), None)
            if existing or len(comments) < 100:
                break
        if existing:
            c = await base.get_json(client, "PATCH", f"{api}/repos/{repo}/issues/comments/{existing['id']}",
                                    "GitHub", json={"body": body})
            return str(c.get("id")), c.get("html_url") or "", True
        c = await base.get_json(client, "POST", f"{api}/repos/{repo}/issues/{number}/comments", "GitHub",
                                json={"body": body})
        return str(c.get("id")), c.get("html_url") or "", False


# ── analysis and the comment ────────────────────────────────────────────────

def proposal(pr: Row) -> str:
    text = f"{pr['title']}\n\n{pr['body']}".strip()
    if pr["files"]:
        text += "\n\nChanged files:\n" + "\n".join(pr["files"])
    return text


def _grounded(entry: Row) -> bool:
    return any(r.startswith(("affects ", "mentions ")) for r in entry.get("reasons", []))


def relevant(report: Row) -> bool:
    """Whether the record has something to say about this PR (see the module docstring)."""
    if any(d.get("relation") in ("conflicts", "changes") for d in report["decisions"]):
        return True
    return any(_grounded(e) and e.get("relation") != "unrelated"
               for e in report["decisions"] + report["risks"] + report["lessons"])


def _link(item: Row) -> str:
    public = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
    title = (item.get("title") or "").replace("\n", " ")
    return f"[{title}]({public}/knowledge/{item['id']})" if public else f"**{title}**"


def render(report: Row, pr: Row) -> str:
    """The comment, in GitHub Markdown."""
    lines = [MARKER, "### Knowledge Hubs: decisions this pull request touches", ""]
    conflicts = [d for d in report["decisions"] if d.get("relation") == "conflicts"]
    changes = [d for d in report["decisions"] if d.get("relation") == "changes"]
    if conflicts:
        lines += [f"> [!WARNING]\n> This change may conflict with {len(conflicts)} recorded decision"
                  f"{'s' if len(conflicts) > 1 else ''}. Worth a look before merging.", ""]

    shown = [d for d in report["decisions"] if d.get("relation") != "unrelated" and (d.get("relation") or _grounded(d))]
    if shown:
        lines.append("**Decisions on record**")
        for d in shown[:_LISTED]:
            label = {"conflicts": "⚠️ may conflict", "changes": "✏️ would change", "consistent": "✅ consistent"}.get(
                d.get("relation") or "", "related")
            line = f"- {_link(d)} ({d.get('status') or 'active'}): {label}"
            if d.get("relation_reason"):
                line += f". {d['relation_reason']}"
            lines.append(line)
            if d.get("why"):
                lines.append(f"  - Why it was decided: {str(d['why'])[:300]}")
            if d.get("replaced_by"):
                lines.append(f"  - Since replaced by {_link(d['replaced_by'])}")
        lines.append("")

    systems = report["systems"]
    if systems:
        direct = [s["name"] for s in systems if s["depth"] == 0]
        downstream = [s["name"] for s in systems if s["depth"] > 0]
        line = f"**Systems:** {', '.join(direct)}"
        if downstream:
            line += f"; depending on them: {', '.join(downstream[:8])}"
        lines += [line, ""]

    for key, heading in (("risks", "Known risks"), ("lessons", "Lessons learned")):
        entries = [e for e in report[key] if _grounded(e)]
        if entries:
            lines.append(f"**{heading}**")
            lines += [f"- {_link(e)}" + (f" ({e['status']})" if e.get("status") else "") for e in entries[:_LISTED]]
            lines.append("")

    open_commitments = report["commitments"]
    if open_commitments and (conflicts or changes):
        lines += [f"**Commitments in flight on the same ground:** {len(open_commitments)}", ""]

    lines.append("<sub>Advisory only: Knowledge Hubs comments and changes nothing. "
                 + ("" if report.get("llm_used") else "No model assessed conflicts for this one. ")
                 + "Updated when the pull request changes.</sub>")
    return "\n".join(lines)


async def analyze(session: ArcadeSession, space_id: str, connector: Row, number: int) -> Row:
    module = REGISTRY.get(connector.get("kind"))
    if connector.get("kind") != "github" or not module:
        raise CopilotError("The Decision Copilot works with GitHub connectors")
    try:
        config = decrypted_config(module, connector)
        pr = await fetch_pr(config, number)
    except ConnectorError as exc:
        raise CopilotError(str(exc)) from exc
    report = await impact.analyze(session, space_id, proposal(pr), [], None)
    return {"pr": {k: pr[k] for k in ("number", "title", "url", "head_sha", "draft", "state")},
            "files": len(pr["files"]), "report": report, "relevant": relevant(report),
            "comment": render(report, pr), "_config": config}


async def run(session: ArcadeSession, space_id: str, connector: Row, number: int, source: str,
              post: Optional[bool] = None) -> Row:
    """
    Analyse the PR and record the run. `post`: True posts regardless, False never,
    None posts only if relevant. Commits.
    """
    try:
        result = await analyze(session, space_id, connector, number)
    except CopilotError as exc:
        return await _record(session, space_id, connector, number, source, "failed", reason=str(exc))
    pr = result["pr"]
    should = result["relevant"] if post is None else post
    if not should:
        return await _record(session, space_id, connector, number, source, "skipped", pr=pr,
                             reason="Nothing on record bears on this change", report=result["report"])
    try:
        comment_id, url, updated = await upsert_comment(result["_config"], number, result["comment"])
    except ConnectorError as exc:
        return await _record(session, space_id, connector, number, source, "failed", pr=pr,
                             reason=f"Couldn't post the comment: {exc}", report=result["report"])
    return await _record(session, space_id, connector, number, source, "updated" if updated else "posted",
                         pr=pr, report=result["report"], comment_url=url)


async def already_done(session: ArcadeSession, space_id: str, connector_id: str, number: int, sha: Optional[str]) -> bool:
    if not sha:
        return False
    return bool(await session.query_one(
        "SELECT id FROM CopilotRun WHERE user_id = :u AND connector_id = :c AND pr_number = :n AND head_sha = :s "
        "AND status IN ['posted', 'updated', 'skipped']",
        {"u": space_id, "c": connector_id, "n": number, "s": sha}))


async def _record(session: ArcadeSession, space_id: str, connector: Row, number: int, source: str, status: str,
                  pr: Optional[Row] = None, reason: str = "", report: Optional[Row] = None, comment_url: str = "") -> Row:
    run_row = {"id": f"cop_{uuid.uuid4().hex[:16]}", "user_id": space_id, "connector_id": connector["id"],
               "pr_number": number, "pr_title": (pr or {}).get("title"), "pr_url": (pr or {}).get("url"),
               "head_sha": (pr or {}).get("head_sha"), "source": source, "status": status, "reason": reason or None,
               "comment_url": comment_url or None, "counts": (report or {}).get("counts") or {}, "created_at": _now()}
    await session.execute("INSERT INTO CopilotRun CONTENT :doc", {"doc": run_row})
    await session.commit()
    return {k: v for k, v in run_row.items() if k != "user_id"}


async def runs(session: ArcadeSession, space_id: str, limit: int = 50) -> List[Row]:
    return await session.query(
        "SELECT id, connector_id, pr_number, pr_title, pr_url, head_sha, source, status, reason, comment_url, "
        "counts, created_at FROM CopilotRun WHERE user_id = :u ORDER BY created_at DESC LIMIT :k",
        {"u": space_id, "k": limit})


async def webhook_job(client: ArcadeClient, space_id: str, connector_id: str, number: int, sha: Optional[str]) -> None:
    """
    A webhook's run, after the response went back to GitHub (in this process, or on
    the Celery worker: coordination.dispatch_copilot). Its own session; never raises.
    One analysis per pull request at a time, across processes: GitHub often sends
    two events at once (opened, then edited), and both would post a comment.
    """
    async with coordination.held_lock(coordination.copilot_lock(connector_id, number),
                                      coordination.COPILOT_LOCK_TTL) as token:
        if token is None:
            logger.info("Copilot: PR #%s of connector %s is already being analysed; skipping", number, connector_id)
            return
        await _webhook_job_locked(client, space_id, connector_id, number, sha)


async def _webhook_job_locked(client: ArcadeClient, space_id: str, connector_id: str, number: int,
                              sha: Optional[str]) -> None:
    session = ArcadeSession(client)
    try:
        connector = await ConnectorRepository(session).get_owned(space_id, connector_id)
        if not connector or not settings(connector)["enabled"]:
            return
        if await already_done(session, space_id, connector_id, number, sha):
            return
        # runs on nobody's request: use the model of whoever set the connector up
        creator = await UserRepository(session).get(connector.get("created_by") or "")
        use_model((creator or {}).get("llm_model"))
        await run(session, space_id, connector, number, "webhook")
    except Exception:
        logger.exception("Copilot webhook run for connector %s, PR #%s failed", connector_id, number)
    finally:
        await session.close()
