"""
Activity timestamps used by inactivity retention (see services/retention.py).
No app imports, so auth, spaces and repositories can use it without cycles.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.arcadedb import ArcadeClient

logger = logging.getLogger(__name__)

Row = Dict[str, Any]

# activity is written at most this often per user / group
TOUCH_INTERVAL = timedelta(hours=1)


def inactive_days() -> int:
    try:
        return max(1, int(os.getenv("RETENTION_INACTIVE_DAYS", "365")))
    except ValueError:
        return 365


def now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def last_activity(row: Row) -> Optional[datetime]:
    """The most recent of last_active_at / last_login_at / created_at."""
    stamps = [_parse(row.get(k)) for k in ("last_active_at", "last_login_at", "created_at")]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def deletes_on(row: Row) -> Optional[str]:
    last = last_activity(row)
    return (last + timedelta(days=inactive_days())).isoformat() if last else None


def _stale(ts: Optional[str], at: datetime) -> bool:
    last = _parse(ts)
    return last is None or at - last >= TOUCH_INTERVAL


async def touch_user(client: ArcadeClient, user: Row, login: bool = False) -> None:
    at = now()
    if not login and not _stale(user.get("last_active_at"), at):
        return
    fields = "last_active_at = :t" + (", last_login_at = :t" if login else "")
    try:
        await client.command(f"UPDATE User SET {fields} WHERE id = :id", {"t": at.isoformat(), "id": user["id"]})
    except Exception as exc:   # activity tracking must never break a request
        logger.warning("Could not record activity for user %s: %s", user.get("id"), exc)


async def touch_group(client: ArcadeClient, group: Row) -> None:
    at = now()
    if not _stale(group.get("last_active_at"), at):
        return
    try:
        await client.command("UPDATE GroupSpace SET last_active_at = :t WHERE id = :id",
                             {"t": at.isoformat(), "id": group["id"]})
    except Exception as exc:
        logger.warning("Could not record activity for group %s: %s", group.get("id"), exc)
