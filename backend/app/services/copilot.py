"""
Decision Copilot: the decisions a change touches, posted where the change is
being discussed. Advisory only: it writes one comment per pull request or
issue, edits that comment as the change evolves, and never approves, requests
changes, transitions, assigns, labels, merges or edits anything else.

Where (services/copilot_targets.py): GitHub pull requests, Jira issues and
Linear issues, through the connector's own address and credentials.

What: the title, description (and a PR's changed file paths) go through
impact analysis (services/impact.py). A comment is posted only when the record
has something to say:
  - a current decision the change conflicts with or changes (the model's call), or
  - knowledge tied to a system the change touches (linked or named), unless the
    model judged it unrelated.
The same text isn't analysed twice: a webhook for an edit that changes none of
it, or for the Copilot's own comment, does nothing.

When: on demand from the app (preview, then post), or from the platform's
webhook. Webhooks are verified (see verify) and answered at once; the analysis
runs afterwards, on the Celery worker in worker mode.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app import coordination, crypto
from app.arcadedb import ArcadeClient, ArcadeSession
from app.repositories import ConnectorRepository, UserRepository
from app.services import impact
from app.services.connectors import REGISTRY
from app.services.connectors.base import ConnectorError
from app.services.connectors.sync import decrypted_config, secret_context
from app.services.copilot_targets import ADAPTERS, MARKER, Target
from app.services.providers import use_model

logger = logging.getLogger(__name__)

Row = Dict[str, Any]

KINDS = tuple(ADAPTERS)                 # connector kinds the Copilot works with
PR_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review", "edited"}
JIRA_EVENTS = {"jira:issue_created", "jira:issue_updated"}
LINEAR_ACTIONS = {"create", "update"}
LINEAR_MAX_AGE_MS = 5 * 60 * 1000       # older signed Linear deliveries are replays
_LISTED = 5                             # entries per section of a comment

__all__ = ["MARKER", "KINDS", "CopilotError", "relevant"]


class CopilotError(Exception):
    """Shown to the user (or recorded on a webhook run)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def adapter(connector: Row):
    a = ADAPTERS.get(connector.get("kind"))
    if not a:
        raise CopilotError("The Decision Copilot works with GitHub, Jira and Linear connectors")
    return a


# ── settings and verification ───────────────────────────────────────────────

def settings(connector: Row) -> Row:
    s = connector.get("copilot") or {}
    return {"enabled": bool(s.get("enabled")), "has_secret": bool(s.get("webhook_secret"))}


def _secret_context(connector_id: str) -> str:
    return secret_context(connector_id) + ":copilot"


def seal_secret(connector_id: str, plain: Optional[str] = None) -> Tuple[str, str]:
    """(plain secret, encrypted value to store). Without `plain`, a new random one."""
    plain = plain or secrets.token_urlsafe(32)
    return plain, crypto.encrypt(plain, _secret_context(connector_id))


def _secret(connector: Row) -> Optional[str]:
    stored = (connector.get("copilot") or {}).get("webhook_secret")
    if not stored:
        return None
    try:
        return crypto.decrypt(stored, _secret_context(connector["id"]))
    except Exception:
        logger.warning("Copilot: the webhook secret of connector %s can't be decrypted", connector["id"])
        return None


def _hmac(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(connector: Row, body: bytes, headers: Row) -> bool:
    """
      github   X-Hub-Signature-256: sha256=HMAC-SHA256(secret, body)
      jira     X-Hub-Signature: sha256=… (Jira Cloud webhooks with a secret), or, for senders
               that can't sign (Jira Automation's "Send web request"), the secret itself in
               X-Knowledge-Hubs-Token
      linear   Linear-Signature: HMAC-SHA256 hex with the signing secret Linear shows, and a
               webhookTimestamp no older than five minutes
    """
    secret = _secret(connector)
    if not secret:
        return False
    kind = connector.get("kind")
    if kind == "github":
        return hmac.compare_digest(headers.get("x-hub-signature-256", ""), "sha256=" + _hmac(secret, body))
    if kind == "jira":
        signature = headers.get("x-hub-signature", "")
        if signature:
            return hmac.compare_digest(signature, "sha256=" + _hmac(secret, body))
        return hmac.compare_digest(headers.get("x-knowledge-hubs-token", "").encode(), secret.encode())
    if kind == "linear":
        if not hmac.compare_digest(headers.get("linear-signature", ""), _hmac(secret, body)):
            return False
        try:
            sent = int(json.loads(body).get("webhookTimestamp") or 0)
        except (ValueError, AttributeError, TypeError):
            return False
        return abs(time.time() * 1000 - sent) <= LINEAR_MAX_AGE_MS
    return False


def target_of_event(kind: str, headers: Row, event: Row) -> Optional[str]:
    """The pull request or issue a webhook event is about, if it's one the Copilot acts on."""
    if kind == "github":
        pr = event.get("pull_request") or {}
        if headers.get("x-github-event") == "pull_request" and event.get("action") in PR_ACTIONS \
                and pr.get("state") == "open" and not pr.get("draft") and isinstance(pr.get("number"), int):
            return str(pr["number"])
    if kind == "jira" and event.get("webhookEvent") in JIRA_EVENTS:
        return (event.get("issue") or {}).get("key")
    if kind == "linear" and event.get("type") == "Issue" and event.get("action") in LINEAR_ACTIONS:
        return (event.get("data") or {}).get("identifier")
    return None


# ── the analysis and the comment ────────────────────────────────────────────

def _grounded(entry: Row) -> bool:
    return any(r.startswith(("affects ", "mentions ")) for r in entry.get("reasons", []))


def relevant(report: Row) -> bool:
    """Whether the record has something to say (see the module docstring)."""
    if any(d.get("relation") in ("conflicts", "changes") for d in report["decisions"]):
        return True
    return any(_grounded(e) and e.get("relation") != "unrelated"
               for e in report["decisions"] + report["risks"] + report["lessons"])


def _url(item: Row) -> Optional[str]:
    public = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
    return f"{public}/knowledge/{item['id']}" if public else None


def blocks(report: Row, noun: str) -> List[tuple]:
    """The comment's content, independent of any platform's markup (copilot_targets renders it)."""
    out: List[tuple] = [("heading", f"Knowledge Hubs: decisions this {noun} touches"), ("blank",)]
    conflicts = [d for d in report["decisions"] if d.get("relation") == "conflicts"]
    changes = [d for d in report["decisions"] if d.get("relation") == "changes"]
    if conflicts:
        out += [("warning", f"This change may conflict with {len(conflicts)} recorded decision"
                            f"{'s' if len(conflicts) > 1 else ''}. Worth a look before going ahead."), ("blank",)]

    shown = [d for d in report["decisions"] if d.get("relation") != "unrelated" and (d.get("relation") or _grounded(d))]
    if shown:
        out.append(("bold", "Decisions on record"))
        for d in shown[:_LISTED]:
            label = {"conflicts": "may conflict", "changes": "would change", "consistent": "consistent"}.get(
                d.get("relation") or "", "related")
            subs = [f"{d.get('status') or 'active'}, {label}" + (f": {d['relation_reason']}" if d.get("relation_reason") else "")]
            if d.get("why"):
                subs.append(f"Why it was decided: {str(d['why'])[:300]}")
            if d.get("replaced_by"):
                subs.append(f"Since replaced by: {d['replaced_by']['title']}")
            out.append(("item", (d.get("title") or "").replace("\n", " "), _url(d), subs))
        out.append(("blank",))

    systems = report["systems"]
    if systems:
        line = "**Systems:** " + ", ".join(s["name"] for s in systems if s["depth"] == 0)
        downstream = [s["name"] for s in systems if s["depth"] > 0]
        if downstream:
            line += "; depending on them: " + ", ".join(downstream[:8])
        out += [("line", line), ("blank",)]

    for key, heading in (("risks", "Known risks"), ("lessons", "Lessons learned")):
        entries = [e for e in report[key] if _grounded(e)]
        if entries:
            out.append(("bold", heading))
            out += [("item", (e.get("title") or "").replace("\n", " "), _url(e), [e["status"]] if e.get("status") else [])
                    for e in entries[:_LISTED]]
            out.append(("blank",))

    if report["commitments"] and (conflicts or changes):
        out += [("line", f"**Commitments in flight on the same ground:** {len(report['commitments'])}"), ("blank",)]

    out.append(("note", "Advisory only: Knowledge Hubs comments and changes nothing. "
                + ("" if report.get("llm_used") else "No model assessed conflicts for this one. ")
                + f"Updated when the {noun} changes."))
    return out


async def _fetch(connector: Row, ref: str) -> Tuple[Any, Row, Target]:
    a = adapter(connector)
    module = REGISTRY.get(connector["kind"])
    try:
        config = decrypted_config(module, connector)
        return a, config, await a.fetch(config, str(ref))
    except ConnectorError as exc:
        raise CopilotError(str(exc)) from exc


async def analyze(session: ArcadeSession, space_id: str, connector: Row, ref: str) -> Row:
    a, config, target = await _fetch(connector, ref)
    report = await impact.analyze(session, space_id, target.proposal(), [], None)
    return {"target": {"ref": target.ref, "title": target.title, "url": target.url, "noun": target.noun,
                       "skip": target.skip},
            "files": len(target.files), "report": report, "relevant": relevant(report),
            "comment": a.render(blocks(report, target.noun)),
            "_adapter": a, "_config": config, "_target": target}


async def run(session: ArcadeSession, space_id: str, connector: Row, ref: str, source: str,
              post: Optional[bool] = None) -> Optional[Row]:
    """
    Analyse and record the run. `post`: True posts regardless (someone asked), None
    posts only if relevant. A webhook run (`source` "webhook") for text already
    analysed, or for a draft or closed one, does nothing and returns None. Commits.
    """
    try:
        result = await analyze(session, space_id, connector, ref)
    except CopilotError as exc:
        return await _record(session, space_id, connector, str(ref), source, "failed", reason=str(exc))
    target: Target = result["_target"]
    if source == "webhook":
        if target.skip:
            return None
        if await _already_analysed(session, space_id, connector["id"], target.ref, target.revision()):
            return None
    should = result["relevant"] if post is None else post
    if not should:
        return await _record(session, space_id, connector, target.ref, source, "skipped", target=target,
                             reason="Nothing on record bears on this change", report=result["report"])
    previous = await _last_comment(session, space_id, connector["id"], target.ref)
    try:
        comment_id, url, updated = await result["_adapter"].comment(result["_config"], target, result["comment"], previous)
    except ConnectorError as exc:
        return await _record(session, space_id, connector, target.ref, source, "failed", target=target,
                             reason=f"Couldn't post the comment: {exc}", report=result["report"])
    return await _record(session, space_id, connector, target.ref, source, "updated" if updated else "posted",
                         target=target, report=result["report"], comment_id=comment_id, comment_url=url)


async def _already_analysed(session: ArcadeSession, space_id: str, connector_id: str, ref: str, revision: str) -> bool:
    return bool(await session.query_one(
        "SELECT id FROM CopilotRun WHERE user_id = :u AND connector_id = :c AND ref = :r AND revision = :v "
        "AND status IN ['posted', 'updated', 'skipped']",
        {"u": space_id, "c": connector_id, "r": ref, "v": revision}))


async def _last_comment(session: ArcadeSession, space_id: str, connector_id: str, ref: str) -> Optional[str]:
    row = await session.query_one(
        "SELECT comment_id FROM CopilotRun WHERE user_id = :u AND connector_id = :c AND ref = :r "
        "AND comment_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
        {"u": space_id, "c": connector_id, "r": ref})
    return (row or {}).get("comment_id")


async def _record(session: ArcadeSession, space_id: str, connector: Row, ref: str, source: str, status: str,
                  target: Optional[Target] = None, reason: str = "", report: Optional[Row] = None,
                  comment_id: Optional[str] = None, comment_url: str = "") -> Row:
    row = {"id": f"cop_{uuid.uuid4().hex[:16]}", "user_id": space_id, "connector_id": connector["id"],
           "kind": connector.get("kind"), "ref": ref, "title": target.title if target else None,
           "url": target.url if target else None, "revision": target.revision() if target else None,
           "source": source, "status": status, "reason": reason or None, "comment_id": comment_id,
           "comment_url": comment_url or None, "counts": (report or {}).get("counts") or {}, "created_at": _now()}
    await session.execute("INSERT INTO CopilotRun CONTENT :doc", {"doc": row})
    await session.commit()
    return {k: v for k, v in row.items() if k not in ("user_id", "comment_id")}


async def runs(session: ArcadeSession, space_id: str, limit: int = 50) -> List[Row]:
    return await session.query(
        "SELECT id, connector_id, kind, ref, title, url, source, status, reason, comment_url, counts, created_at "
        "FROM CopilotRun WHERE user_id = :u ORDER BY created_at DESC LIMIT :k", {"u": space_id, "k": limit})


# ── after a webhook ─────────────────────────────────────────────────────────

def copilot_lock(connector_id: str, ref: str) -> str:
    return f"copilot:{connector_id}:{ref}"


async def webhook_job(client: ArcadeClient, space_id: str, connector_id: str, ref: str) -> None:
    """
    A webhook's run, after the response went back (in this process, or on the Celery
    worker: coordination.dispatch_copilot). Its own session; never raises. One
    analysis per pull request or issue at a time, across processes: platforms often
    send two events at once (GitHub: opened, then edited), and both would comment.
    """
    async with coordination.held_lock(copilot_lock(connector_id, ref), coordination.COPILOT_LOCK_TTL) as token:
        if token is None:
            logger.info("Copilot: %s of connector %s is already being analysed; skipping", ref, connector_id)
            return
        session = ArcadeSession(client)
        try:
            connector = await ConnectorRepository(session).get_owned(space_id, connector_id)
            if not connector or not settings(connector)["enabled"]:
                return
            # runs on nobody's request: use the model of whoever set the connector up
            creator = await UserRepository(session).get(connector.get("created_by") or "")
            use_model((creator or {}).get("llm_model"))
            await run(session, space_id, connector, ref, "webhook")
        except Exception:
            logger.exception("Copilot webhook run for connector %s, %s failed", connector_id, ref)
        finally:
            await session.close()
