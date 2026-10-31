"""
Slack: the /decisions slash command. Someone types

    /decisions move the payment service to MongoDB

and gets back what the record says about that change: decisions on record and
any the change may conflict with, the systems it reaches, risks and lessons
(services/impact.py, rendered like a Copilot comment).

Least privilege by design: a slash command answers through the response_url
Slack sends with it, so Knowledge Hubs holds no bot token and can't read or
post anything in Slack on its own. It needs only the app's signing secret, to
verify each request (X-Slack-Signature over "v0:<timestamp>:<body>", with a
timestamp under five minutes old). The response_url must be Slack's own
(https://hooks.slack.com/…), or nothing is sent.

Answers are ephemeral (only the person who asked sees them) unless the text
starts with "share", which posts in the channel.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app import crypto
from app.arcadedb import ArcadeClient, ArcadeSession
from app.repositories import UserRepository
from app.services import copilot, impact
from app.services.connectors import base
from app.services.connectors.base import ConnectorError
from app.services.copilot_targets import slack_mrkdwn
from app.services.providers import use_model

logger = logging.getLogger(__name__)

Row = Dict[str, Any]

SLACK_HOOKS = "hooks.slack.com"
MAX_AGE_SECONDS = 300
COMMAND = "/decisions"
HELP = (f"*{COMMAND}* tells you what your knowledge base says about a change before you make it.\n"
        f"• `{COMMAND} move the payment service to MongoDB`: only you see the answer\n"
        f"• `{COMMAND} share move the payment service to MongoDB`: posted in the channel\n"
        "It reads decisions, risks and lessons; it never changes anything.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _context(integration_id: str) -> str:
    return f"slack:{integration_id}:signing_secret"


# ── integrations ────────────────────────────────────────────────────────────

async def list_integrations(session: ArcadeSession, space_id: str) -> List[Row]:
    return await session.query(
        "SELECT id, name, created_at, created_by_name, last_used_at FROM SlackIntegration "
        "WHERE user_id = :u ORDER BY created_at", {"u": space_id})


async def create(session: ArcadeSession, space_id: str, name: str, signing_secret: str,
                 user_id: str, username: str) -> Row:
    sid = f"slk_{uuid.uuid4().hex[:16]}"
    row = {"id": sid, "user_id": space_id, "name": name, "created_by": user_id, "created_by_name": username,
           "signing_secret": crypto.encrypt(signing_secret, _context(sid)), "created_at": _now()}
    await session.execute("INSERT INTO SlackIntegration CONTENT :doc", {"doc": row})
    await session.commit()
    return {k: row[k] for k in ("id", "name", "created_at", "created_by_name")} | {"last_used_at": None}


async def set_secret(session: ArcadeSession, space_id: str, integration_id: str, signing_secret: str) -> bool:
    found = await session.query_one("SELECT id FROM SlackIntegration WHERE id = :id AND user_id = :u",
                                    {"id": integration_id, "u": space_id})
    if found:
        await session.execute("UPDATE SlackIntegration SET signing_secret = :s WHERE id = :id",
                              {"s": crypto.encrypt(signing_secret, _context(integration_id)), "id": integration_id})
        await session.commit()
    return bool(found)


async def delete(session: ArcadeSession, space_id: str, integration_id: str) -> bool:
    found = await session.query_one("SELECT id FROM SlackIntegration WHERE id = :id AND user_id = :u",
                                    {"id": integration_id, "u": space_id})
    if found:
        await session.execute("DELETE FROM SlackIntegration WHERE id = :id", {"id": integration_id})
        await session.commit()
    return bool(found)


# ── requests from Slack ─────────────────────────────────────────────────────

def verify(integration: Row, body: bytes, timestamp: str, signature: str, now: Optional[float] = None) -> bool:
    """Slack's request signing: v0=HMAC-SHA256(secret, "v0:<timestamp>:<body>"), recent timestamp."""
    try:
        sent = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((now or time.time()) - sent) > MAX_AGE_SECONDS:
        return False
    try:
        secret = crypto.decrypt(integration.get("signing_secret") or "", _context(integration["id"]))
    except Exception:
        logger.warning("Slack: the signing secret of integration %s can't be decrypted", integration["id"])
        return False
    expected = "v0=" + hmac.new(secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def parse(text: str) -> Tuple[str, bool]:
    """(the change to look up, whether to share it in the channel)"""
    words = text.strip().split(None, 1)
    if words and words[0].lower() == "share":
        return (words[1] if len(words) > 1 else "").strip(), True
    return text.strip(), False


def response_url_ok(url: str) -> bool:
    try:
        parsed = httpx.URL(url)
    except Exception:
        return False
    return parsed.scheme == "https" and parsed.host == SLACK_HOOKS


def message(report: Row, change: str, asked_by: str) -> Row:
    """The reply: Slack mrkdwn, from the same blocks as a Copilot comment."""
    if not copilot.relevant(report):
        text = f"Nothing on record bears on _{change}_: no decision, risk or lesson about it, or about the systems it names."
    else:
        text = slack_mrkdwn(copilot.blocks(
            report, "change", heading=f"What the record says about: {change}",
            note=f"Asked by {asked_by}. Advisory only: Knowledge Hubs read this and changed nothing. {{assessed}}"))
    return {"text": text[:3000], "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}]}


async def reply(response_url: str, payload: Row) -> None:
    """POST to Slack's response_url, confined to hooks.slack.com."""
    async with base.make_client({SLACK_HOOKS}) as client:
        response = await client.post(response_url, json=payload)
        if response.status_code >= 400:
            raise ConnectorError(f"Slack answered {response.status_code}: {response.text[:200]}")


async def answer_job(client: ArcadeClient, space_id: str, integration_id: str, text: str, response_url: str,
                     asked_by: str) -> None:
    """The analysis after Slack got its quick acknowledgement. Own session; never raises."""
    change, share = parse(text)
    session = ArcadeSession(client)
    try:
        integration = await session.query_one(
            "SELECT id, created_by FROM SlackIntegration WHERE id = :id AND user_id = :u",
            {"id": integration_id, "u": space_id})
        if not integration:
            return
        creator = await UserRepository(session).get(integration.get("created_by") or "")
        use_model((creator or {}).get("llm_model"))
        report = await impact.analyze(session, space_id, change, [], None)
        payload = {**message(report, change, asked_by), "replace_original": True,
                   "response_type": "in_channel" if share else "ephemeral"}
        await session.execute("UPDATE SlackIntegration SET last_used_at = :t WHERE id = :id",
                              {"t": _now(), "id": integration_id})
        await session.commit()
        await reply(response_url, payload)
    except Exception:
        logger.exception("Slack /decisions for integration %s failed", integration_id)
        try:
            await reply(response_url, {"response_type": "ephemeral", "replace_original": True,
                                       "text": "Sorry, Knowledge Hubs couldn't look that up just now. Try again shortly."})
        except Exception:
            pass
    finally:
        await session.close()
