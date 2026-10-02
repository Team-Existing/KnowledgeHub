"""
Inactivity retention: users and groups unused for RETENTION_INACTIVE_DAYS
(default 365) are deleted with their data.

Activity
--------
- A user is active when they sign in or make any authenticated request
  (`User.last_active_at`; sign-ins also set `last_login_at`).
- A group is active when any member makes a request in its space
  (`GroupSpace.last_active_at`).
Both are written at most once per TOUCH_INTERVAL, outside the request's
transaction, so reads stay cheap. Accounts and groups that existed before
tracking started get "now" on upgrade (see app/migrations.py), so nobody is
deleted for inactivity that was never measured.

What a deletion removes
-----------------------
- User: their personal space (every vertex/edge/document whose owner id is
  theirs), their memberships and invitations, the GraphRAG questions they
  asked in groups, and the account. What they added to groups stays in the
  group (it belongs to the group). Groups they were admin of pass to the
  longest-standing remaining member, or are deleted if no member is left.
- Group: the group, its memberships, and everything in its space.

Safety
------
- The last server admin is never deleted (nobody could administer the hub).
- Spaces with a connector sync in progress are skipped until the next run.
- Server admins can see what's due (GET /admin/retention) and run it now.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app import coordination
from app.activity import deletes_on, inactive_days, last_activity, now
from app.arcadedb import ArcadeClient, ArcadeSession
from app.rbac import Role
from app.repositories.groups import GROUP_ADMIN, GroupRepository

logger = logging.getLogger(__name__)

Row = Dict[str, Any]

# documents whose owner id (user_id) may be a personal space or a group
_SPACE_DOCUMENTS = ("ItemEvent", "SyncedDocument", "Connector", "Playbook", "QueryLog")


def enabled() -> bool:
    return os.getenv("RETENTION_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")


def check_interval() -> timedelta:
    try:
        return timedelta(hours=max(1.0, float(os.getenv("RETENTION_CHECK_HOURS", "24"))))
    except ValueError:
        return timedelta(hours=24)


# ── planning ────────────────────────────────────────────────────────────────

def _brief_user(u: Row) -> Row:
    return {"id": u["id"], "username": u["username"], "role": u.get("role") or Role.MEMBER.value,
            "last_active_at": (last_activity(u) or now()).isoformat(), "deletes_on": deletes_on(u)}


def _brief_group(g: Row, members: int) -> Row:
    return {"id": g["id"], "name": g["name"], "members": members,
            "last_active_at": (last_activity(g) or now()).isoformat(), "deletes_on": deletes_on(g)}


async def plan(session: ArcadeSession, at: Optional[datetime] = None, horizon_days: int = 0) -> Row:
    """
    Users and groups whose deletion date is on or before at + horizon_days.
    horizon 0 = due now; the admin page also shows the next 30 days.
    """
    at = at or now()
    cutoff = at - timedelta(days=inactive_days()) + timedelta(days=horizon_days)
    users = await session.query(
        "SELECT id, username, role, created_at, last_active_at, last_login_at FROM User ORDER BY created_at")
    groups = await session.query("SELECT id, name, created_at, last_active_at FROM GroupSpace ORDER BY created_at")
    due_users = [u for u in users if (last_activity(u) or at) <= cutoff]
    due_groups = []
    for g in groups:
        if (last_activity(g) or at) <= cutoff:
            n = await session.scalar("SELECT count(*) AS n FROM Membership WHERE group_id = :g AND status = 'active'",
                                     {"g": g["id"]}, 0)
            due_groups.append(_brief_group(g, n))
    return {"users": [_brief_user(u) for u in due_users], "groups": due_groups}


# ── deleting ────────────────────────────────────────────────────────────────

async def _space_is_syncing(session: ArcadeSession, space_id: str) -> bool:
    from app.services.connectors.sync import is_running
    rows = await session.query("SELECT id FROM Connector WHERE user_id = :s", {"s": space_id})
    return any([await is_running(r["id"]) for r in rows])


async def delete_space_data(session: ArcadeSession, space_id: str) -> None:
    p = {"s": space_id}
    await session.execute("DELETE VERTEX FROM Node WHERE user_id = :s", p)   # edges go with their vertices
    for doc in _SPACE_DOCUMENTS:
        await session.execute(f"DELETE FROM {doc} WHERE user_id = :s", p)


async def delete_user(session: ArcadeSession, user_id: str) -> Row:
    """Delete an account and its data; hand over or delete groups it administered. Commits."""
    groups = GroupRepository(session)
    handed_over, groups_deleted = [], []
    admin_of = await session.query(
        "SELECT group_id FROM Membership WHERE user_id = :u AND role = :r AND status = 'active'",
        {"u": user_id, "r": GROUP_ADMIN})
    for row in admin_of:
        gid = row["group_id"]
        successor = await session.query_one(
            "SELECT user_id FROM Membership WHERE group_id = :g AND status = 'active' AND user_id <> :u "
            "ORDER BY responded_at, created_at LIMIT 1", {"g": gid, "u": user_id})
        if successor:
            await groups.set_membership(gid, successor["user_id"], role=GROUP_ADMIN)
            handed_over.append({"group_id": gid, "new_admin": successor["user_id"]})
        else:
            group = await groups.get(gid)
            await groups.delete_everything(gid)
            groups_deleted.append(group["name"] if group else gid)
    await delete_space_data(session, user_id)
    await session.execute("DELETE FROM QueryLog WHERE asked_by = :u", {"u": user_id})   # their questions in groups
    await session.execute("DELETE FROM Membership WHERE user_id = :u", {"u": user_id})
    await session.execute("DELETE FROM User WHERE id = :u", {"u": user_id})
    await session.commit()
    return {"handed_over": handed_over, "groups_deleted": groups_deleted}


async def run(session: ArcadeSession, at: Optional[datetime] = None) -> Row:
    """
    Delete everything that's due. Returns a report (also stored in Meta
    'retention_last_run'). Holds a lock shared by all processes, so the
    scheduled run and an admin's "run now" never overlap.
    """
    async with coordination.held_lock("retention", coordination.RETENTION_LOCK_TTL) as token:
        if token is None:
            return {"ran_at": (at or now()).isoformat(), "inactive_days": inactive_days(), "users_deleted": [],
                    "groups_deleted": [], "skipped": [{"reason": "another clean-up run is in progress"}]}
        return await _run_locked(session, at)


async def _run_locked(session: ArcadeSession, at: Optional[datetime]) -> Row:
    at = at or now()
    due = await plan(session, at)
    report: Row = {"ran_at": at.isoformat(), "inactive_days": inactive_days(),
                   "users_deleted": [], "groups_deleted": [], "skipped": []}

    for g in due["groups"]:
        if await _space_is_syncing(session, g["id"]):
            report["skipped"].append({"group": g["name"], "reason": "a connector is syncing"})
            continue
        await GroupRepository(session).delete_everything(g["id"])
        await session.commit()
        report["groups_deleted"].append(g["name"])

    admins_left = await session.scalar("SELECT count(*) AS n FROM User WHERE role = :r",
                                       {"r": Role.ADMIN.value}, 0)
    for u in due["users"]:
        if u["role"] == Role.ADMIN.value and admins_left <= 1:
            report["skipped"].append({"user": u["username"], "reason": "the last server admin is never deleted"})
            continue
        if await _space_is_syncing(session, u["id"]):
            report["skipped"].append({"user": u["username"], "reason": "a connector is syncing"})
            continue
        outcome = await delete_user(session, u["id"])
        if u["role"] == Role.ADMIN.value:
            admins_left -= 1
        report["users_deleted"].append(u["username"])
        # groups they administered alone go with them
        report["groups_deleted"] += outcome["groups_deleted"]

    await session.execute("UPDATE Meta SET key = 'retention_last_run', value = :v UPSERT WHERE key = 'retention_last_run'",
                          {"v": report})
    await session.commit()
    if report["users_deleted"] or report["groups_deleted"]:
        logger.warning("Retention: deleted users %s and groups %s after %d days without activity",
                       report["users_deleted"], report["groups_deleted"], inactive_days())
    return report


async def last_run(session: ArcadeSession) -> Optional[Row]:
    row = await session.query_one("SELECT value FROM Meta WHERE key = 'retention_last_run'")
    return row["value"] if row else None


async def loop(client: ArcadeClient) -> None:
    """
    In-process mode only (no REDIS_URL): started with the API, runs now and then
    every RETENTION_CHECK_HOURS. In worker mode Celery beat schedules it instead.
    """
    while True:
        if enabled():
            session = ArcadeSession(client)
            try:
                await run(session)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Retention run failed; will retry at the next interval")
            finally:
                await session.close()
        await asyncio.sleep(check_interval().total_seconds())
