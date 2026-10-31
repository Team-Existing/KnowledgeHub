"""
Slack channels: where decisions often get made in passing. Each thread is
ingested as a conversation (transcript extraction: decisions, action items
and risks, with who said what); messages outside threads are gathered into
one conversation per channel per day.

Auth: a bot token (xoxb-…) from a Slack app with the channels:history scope
(groups:history for private channels), added to each channel it reads. The
API address is part of the config, like every connector's: https://slack.com/api.
Only what the listed channels hold is read; nothing is posted.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from app.services.connectors import base
from app.services.connectors.base import ConfigField, ConnectorError, FetchResult, Row, SourceDocument, user_url

KIND = "slack"
LABEL = "Slack — channel conversations"
FILESYSTEM = False
FIELDS = [
    ConfigField("api_url", "Slack API address", required=True,
                help="Slack's Web API: https://slack.com/api"),
    ConfigField("bot_token", "Bot token", type="password", required=True, secret=True,
                help="xoxb-… from a Slack app with channels:history (and groups:history for private channels), "
                     "invited to each channel"),
    ConfigField("channels", "Channel IDs", type="list", required=True,
                help="Comma-separated, e.g. C024BE91L (channel details → bottom of the About tab)"),
    ConfigField("days", "Look back (days)", type="number", default=30),
    ConfigField("max_items", "Max conversations per sync", type="number", default=100),
]

MIN_TEXT = 40          # a day's loose messages shorter than this aren't worth a conversation


def check(config: Row) -> None:
    user_url(config["api_url"], "Slack API address")


async def _call(client, api: str, method: str, params: Row) -> Row:
    data = await base.get_json(client, "GET", f"{api}/{method}", "Slack", params=params)
    if not data.get("ok"):
        error = data.get("error") or "request failed"
        hint = {"not_in_channel": " (invite the app to the channel)", "invalid_auth": " (check the bot token)",
                "missing_scope": " (the app needs channels:history / groups:history)"}.get(error, "")
        raise ConnectorError(f"Slack: {error}{hint}")
    return data


def _when(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def _line(message: Row) -> str:
    who = ((message.get("user_profile") or {}).get("real_name") or (message.get("user_profile") or {}).get("name")
           or message.get("username") or message.get("user") or "someone")
    return f"{who}: {(message.get('text') or '').strip()}"


def _link(api: str, channel: str, ts: str) -> str:
    # https://slack.com/archives/<channel>/p<ts without the dot>: Slack redirects it to the workspace
    host = user_url(api, "Slack API address").host
    return f"https://{host}/archives/{channel}/p{ts.replace('.', '')}"


async def fetch(config: Row) -> FetchResult:
    api = str(user_url(config["api_url"], "Slack API address")).rstrip("/")
    host = user_url(api, "Slack API address").host
    oldest = (datetime.now(timezone.utc) - timedelta(days=config["days"])).timestamp()
    limit = config["max_items"]
    docs: List[SourceDocument] = []
    errors: List[Row] = []
    async with base.make_client({host}, headers={"Authorization": f"Bearer {config['bot_token']}"}) as client:
        for channel in config["channels"]:
            if len(docs) >= limit:
                break
            try:
                info = await _call(client, api, "conversations.info", {"channel": channel})
                name = (info.get("channel") or {}).get("name") or channel
                messages: List[Row] = []
                cursor = None
                for _ in range(20):                       # at most 20 pages of 200
                    params: Dict[str, Any] = {"channel": channel, "oldest": f"{oldest:.6f}", "limit": 200}
                    if cursor:
                        params["cursor"] = cursor
                    page = await _call(client, api, "conversations.history", params)
                    messages += page.get("messages") or []
                    cursor = (page.get("response_metadata") or {}).get("next_cursor")
                    if not page.get("has_more") or not cursor:
                        break
            except ConnectorError as exc:
                errors.append({"external_id": channel, "error": str(exc)})
                continue

            loose: Dict[str, List[Row]] = defaultdict(list)
            for m in sorted(messages, key=lambda m: float(m.get("ts") or 0)):
                if m.get("subtype") in ("channel_join", "channel_leave", "bot_message") or not (m.get("text") or "").strip():
                    continue
                if m.get("reply_count") and len(docs) < limit:
                    try:
                        replies = await _call(client, api, "conversations.replies", {"channel": channel, "ts": m["ts"], "limit": 200})
                    except ConnectorError as exc:
                        errors.append({"external_id": f"{channel}:{m['ts']}", "error": str(exc)})
                        continue
                    thread = [r for r in replies.get("messages") or [] if (r.get("text") or "").strip()]
                    first = (m.get("text") or "").strip().splitlines()[0][:80]
                    docs.append(SourceDocument(
                        external_id=f"{channel}:{m['ts']}", title=f"#{name}: {first}",
                        content="\n".join(_line(r) for r in thread), mode="transcript", source_type="slack",
                        url=_link(api, channel, m["ts"]), author=_line(m).split(":", 1)[0],
                        created_at=_when(m["ts"]).isoformat(), tags=["slack", name][:5]))
                elif not m.get("thread_ts") or m.get("thread_ts") == m.get("ts"):
                    loose[_when(m["ts"]).date().isoformat()].append(m)
            for day, day_messages in sorted(loose.items()):
                text = "\n".join(_line(m) for m in day_messages)
                if len(text) < MIN_TEXT or len(docs) >= limit:
                    continue
                docs.append(SourceDocument(
                    external_id=f"{channel}:{day}", title=f"#{name} on {day}", content=text, mode="transcript",
                    source_type="slack", url=_link(api, channel, day_messages[0]["ts"]),
                    author="several", created_at=_when(day_messages[0]["ts"]).isoformat(), tags=["slack", name][:5]))
    return FetchResult(docs[:limit], errors)
