"""
The weekly digest (the Watchtower's report): what in a space needs attention,
posted to a Slack channel through an incoming webhook.

  May be out of date      items with health signals (changed sources, contradictions, flags …)
  Due for review          review dates within two weeks or past
  Overdue commitments     due date passed, not done or dropped
  Outcomes to judge       decisions past their check-by date with no verdict

Each space can have several digests (one per channel), each sent on a chosen
weekday at DIGEST_HOUR (UTC). A digest with nothing to report isn't sent. The
webhook URL must be Slack's (https://hooks.slack.com/…); it is stored encrypted
and never shown again. "Send now" sends regardless of the day.

Scheduling: Celery beat runs it hourly in worker mode; otherwise the API
process checks hourly (loop below).
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app import coordination, crypto
from app.arcadedb import ArcadeClient, ArcadeSession
from app.services import health, tracking
from app.services.connectors import base
from app.services.connectors.base import ConnectorError

logger = logging.getLogger(__name__)

Row = Dict[str, Any]

SLACK_HOOKS = "hooks.slack.com"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_LISTED = 6


def enabled() -> bool:
    """DIGEST_ENABLED=false stops scheduled digests ("Send now" still works)."""
    return os.getenv("DIGEST_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")


def digest_hour() -> int:
    try:
        return min(23, max(0, int(os.getenv("DIGEST_HOUR", "8"))))
    except ValueError:
        return 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _context(digest_id: str) -> str:
    return f"digest:{digest_id}:webhook_url"


def webhook_ok(url: str) -> bool:
    try:
        parsed = httpx.URL(url)
    except Exception:
        return False
    return parsed.scheme == "https" and parsed.host == SLACK_HOOKS and parsed.path.startswith("/services/")


# ── subscriptions ───────────────────────────────────────────────────────────

async def list_digests(session: ArcadeSession, space_id: str) -> List[Row]:
    return await session.query(
        "SELECT id, name, weekday, created_at, created_by_name, last_sent_at, last_status FROM Digest "
        "WHERE user_id = :u ORDER BY created_at", {"u": space_id})


async def create(session: ArcadeSession, space_id: str, name: str, webhook_url: str, weekday: int,
                 username: str) -> Row:
    did = f"dig_{uuid.uuid4().hex[:16]}"
    row = {"id": did, "user_id": space_id, "name": name, "weekday": weekday, "created_by_name": username,
           "webhook_url": crypto.encrypt(webhook_url, _context(did)), "created_at": _now().isoformat()}
    await session.execute("INSERT INTO Digest CONTENT :doc", {"doc": row})
    await session.commit()
    return {k: row[k] for k in ("id", "name", "weekday", "created_at", "created_by_name")} | \
        {"last_sent_at": None, "last_status": None}


async def delete(session: ArcadeSession, space_id: str, digest_id: str) -> bool:
    found = await session.query_one("SELECT id FROM Digest WHERE id = :id AND user_id = :u", {"id": digest_id, "u": space_id})
    if found:
        await session.execute("DELETE FROM Digest WHERE id = :id", {"id": digest_id})
        await session.commit()
    return bool(found)


# ── the content ─────────────────────────────────────────────────────────────

def describe_signal(signal: Row) -> str:
    """One health signal in words (the frontend's services/health.ts says the same)."""
    kind, title = signal.get("kind"), signal.get("item_title")
    return {
        "source_changed": f"its source “{signal.get('source_title') or 'the source'}” changed",
        "removed_from_source": f"its source “{signal.get('source_title') or 'the source'}” no longer contains it",
        "dependency_changed": (signal.get("reason") or "something it builds on changed") + (f": “{title}”" if title else ""),
        "flagged": f"flagged{' by ' + signal['actor'] if signal.get('actor') else ''}: “{signal.get('note') or ''}”",
        "outcome_not_achieved": "its outcome was recorded as not achieved",
        "contradicted": "newer knowledge contradicts it" + (f": “{title}”" if title else ""),
        "possible_duplicate": f"it may duplicate “{title or 'another item'}”",
        "unsupported": "its source doesn't say this" + (f" ({signal['reason']})" if signal.get("reason") else ""),
    }.get(kind, kind or "")

async def compose(session: ArcadeSession, space_id: str) -> Row:
    """{sections: [{title, count, entries: [{id, title, note}]}], total}"""
    attention = await health.attention(session, space_id, limit=200, offset=0)
    stale = [i for i in attention["items"] if i.get("health_signals")]
    review = [i for i in attention["items"] if not i.get("health_signals")]
    overdue = (await tracking.commitments(session, space_id, "overdue", 200, 0))["items"]
    outcomes = [d for d in (await tracking.outcomes(session, space_id))["awaiting"] if d["outcome"]["due"]]

    def entry(item: Row, note: str) -> Row:
        return {"id": item["id"], "title": item.get("title") or "", "note": note}

    sections = [
        {"title": "May be out of date", "entries": [entry(i, describe_signal((i.get("health_signals") or [{}])[-1]))
                                                   for i in stale]},
        {"title": "Due for review", "entries": [entry(i, f"review by {i.get('review_due')}") for i in review]},
        {"title": "Overdue commitments", "entries": [entry(c, f"{c.get('owner') or 'no owner'}, due {c['due_date']}")
                                                    for c in overdue]},
        {"title": "Outcomes to judge", "entries": [entry(d, f"expected: {d['outcome']['expected']}") for d in outcomes]},
    ]
    for s in sections:
        s["count"] = len(s["entries"])
    return {"sections": [s for s in sections if s["count"]], "total": sum(s["count"] for s in sections)}


def slack_message(content: Row, space_name: str) -> Row:
    public = os.getenv("PUBLIC_URL", "").strip().rstrip("/")

    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    lines = [f"*Knowledge Hubs: what needs attention in {esc(space_name)}*"]
    for s in content["sections"]:
        lines.append(f"\n*{s['title']}* ({s['count']})")
        for e in s["entries"][:_LISTED]:
            title = esc(e["title"][:140])
            link = f"<{public}/knowledge/{e['id']}|{title}>" if public else title
            lines.append(f"• {link}" + (f" — {esc(e['note'][:160])}" if e.get("note") else ""))
        if s["count"] > _LISTED:
            lines.append(f"  …and {s['count'] - _LISTED} more")
    text = "\n".join(lines)[:3000]
    return {"text": text, "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]}


# ── sending ─────────────────────────────────────────────────────────────────

async def send(session: ArcadeSession, digest_id: str, force: bool = False) -> Row:
    """Compose and post one digest. Empty ones aren't sent unless forced. Records the outcome. Commits."""
    row = await session.query_one("SELECT FROM Digest WHERE id = :id", {"id": digest_id})
    if not row:
        return {"status": "missing"}
    content = await compose(session, row["user_id"])
    if not content["total"] and not force:
        status = "nothing to report"
    else:
        name = await _space_name(session, row["user_id"])
        try:
            url = crypto.decrypt(row["webhook_url"], _context(digest_id))
            if not webhook_ok(url):
                raise ConnectorError("the webhook URL isn't a Slack incoming webhook")
            async with base.make_client({SLACK_HOOKS}) as client:
                r = await client.post(url, json=slack_message(content, name))
            status = "sent" if r.status_code < 400 else f"failed: Slack answered {r.status_code}"
        except Exception as exc:
            logger.warning("Digest %s failed: %s", digest_id, exc)
            status = f"failed: {exc}"
    await session.execute("UPDATE Digest SET last_sent_at = :t, last_status = :s WHERE id = :id",
                          {"t": _now().isoformat(), "s": status[:200], "id": digest_id})
    await session.commit()
    return {"status": status, "total": content["total"]}


async def _space_name(session: ArcadeSession, space_id: str) -> str:
    for doc in ("Organization", "GroupSpace"):
        row = await session.query_one(f"SELECT name FROM {doc} WHERE id = :id", {"id": space_id})
        if row:
            return row["name"]
    return "your space"


async def send_due(client: ArcadeClient, now: Optional[datetime] = None) -> List[Row]:
    """Every digest whose weekday is today, past DIGEST_HOUR, not yet sent today."""
    now = now or _now()
    if now.hour < digest_hour():
        return []
    session = ArcadeSession(client)
    sent = []
    try:
        for row in await session.query("SELECT id, last_sent_at FROM Digest WHERE weekday = :d", {"d": now.weekday()}):
            if (row.get("last_sent_at") or "")[:10] == now.date().isoformat():
                continue
            sent.append({"id": row["id"], **await send(session, row["id"])})
    finally:
        await session.close()
    return sent


async def run_scheduled(client: ArcadeClient) -> List[Row]:
    """One check, held by a lock shared across processes so no digest goes out twice."""
    if not enabled():
        return []
    async with coordination.held_lock("digests", 600) as token:
        if token is None:
            return []
        return await send_due(client)


async def loop(client: ArcadeClient) -> None:
    """In-process mode: check hourly. Worker mode: Celery beat runs run_scheduled instead."""
    while True:
        try:
            await run_scheduled(client)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Digest check failed; will retry within the hour")
        await asyncio.sleep(3600)
