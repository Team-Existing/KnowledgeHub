"""Inactivity retention: idle users and groups are deleted with their data, safely."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict

import pytest

from app import activity
from tests.helpers import ingest_text

LONG_AGO = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
SOON = (datetime.now(timezone.utc) - timedelta(days=350)).isoformat()   # due within 30 days


# ── rules (no database) ─────────────────────────────────────────────────────

def test_last_activity_is_the_latest_timestamp():
    row = {"created_at": "2024-01-01T00:00:00+00:00", "last_login_at": "2024-06-01T00:00:00+00:00",
           "last_active_at": "2024-03-01T00:00:00+00:00"}
    assert activity.last_activity(row).isoformat().startswith("2024-06-01")
    assert activity.last_activity({}) is None


def test_deletion_date_follows_the_configured_period(monkeypatch):
    monkeypatch.setenv("RETENTION_INACTIVE_DAYS", "30")
    assert activity.deletes_on({"last_active_at": "2025-01-01T00:00:00+00:00"}).startswith("2025-01-31")
    monkeypatch.setenv("RETENTION_INACTIVE_DAYS", "not a number")
    assert activity.inactive_days() == 365


def test_activity_is_written_at_most_hourly():
    at = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
    assert activity._stale(None, at)
    assert not activity._stale((at - timedelta(minutes=30)).isoformat(), at)
    assert activity._stale((at - timedelta(hours=2)).isoformat(), at)


# ── helpers ─────────────────────────────────────────────────────────────────

def me(client, headers) -> Dict:
    return client.get("/auth/me", headers=headers).json()


def backdate_user(db_query, user_id, when=LONG_AGO):
    db_query("UPDATE User SET last_active_at = :t, last_login_at = :t, created_at = :t WHERE id = :id", t=when, id=user_id)


def backdate_group(db_query, group_id, when=LONG_AGO):
    db_query("UPDATE GroupSpace SET last_active_at = :t, created_at = :t WHERE id = :id", t=when, id=group_id)


def run_now(client, admin):
    r = client.post("/admin/retention/run", headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


# ── tracking ────────────────────────────────────────────────────────────────

def test_sign_in_and_requests_record_activity(client, make_user, db_query):
    headers = make_user()
    uid = me(client, headers)["id"]
    row = db_query("SELECT last_login_at, last_active_at FROM User WHERE id = :id", id=uid)[0]
    assert row["last_login_at"] and row["last_active_at"]

    backdate_user(db_query, uid)
    client.get("/knowledge", headers=headers)          # any authenticated request
    fresh = db_query("SELECT last_active_at FROM User WHERE id = :id", id=uid)[0]["last_active_at"]
    assert activity.last_activity({"last_active_at": fresh}) > datetime.now(timezone.utc) - timedelta(minutes=5)


def test_working_in_a_group_keeps_it_alive(client, alice, db_query):
    gid = client.post("/groups", headers=alice, json={"name": "Alive"}).json()["id"]
    backdate_group(db_query, gid)
    client.get("/knowledge", headers={**alice, "X-Space": gid})
    stamp = db_query("SELECT last_active_at FROM GroupSpace WHERE id = :id", id=gid)[0]["last_active_at"]
    assert stamp > SOON


# ── deleting ────────────────────────────────────────────────────────────────

def test_inactive_user_is_deleted_with_personal_data_but_group_work_stays(client, make_user, db_query):
    admin = make_user(role="admin")
    idle, active = make_user(), make_user()
    idle_id = me(client, idle)["id"]
    gid = client.post("/groups", headers=active, json={"name": "Shared"}).json()["id"]
    client.post(f"/groups/{gid}/invitations", headers=active, json={"user_id": idle_id})
    client.post(f"/invitations/{gid}/accept", headers=idle)
    ingest_text(client, idle, title="Idle private", content="We decided idle things.")
    ingest_text(client, {**idle, "X-Space": gid}, title="Idle contribution", content="We decided group things.")
    backdate_user(db_query, idle_id)

    report = run_now(client, admin)
    assert me(client, idle).get("id") is None                                    # token no longer works
    assert client.get("/knowledge", headers=idle).status_code == 401
    assert next(iter(db_query("SELECT count(*) AS n FROM User WHERE id = :id", id=idle_id)))["n"] == 0
    assert db_query("SELECT count(*) AS n FROM Node WHERE user_id = :id", id=idle_id)[0]["n"] == 0
    assert db_query("SELECT count(*) AS n FROM Membership WHERE user_id = :id", id=idle_id)[0]["n"] == 0
    titles = {a["title"] for a in client.get("/knowledge", headers={**active, "X-Space": gid}).json()["artifacts"]}
    assert "Idle contribution" in titles                                         # it belongs to the group
    assert me(client, active)["id"]                                              # the active user is untouched


def test_group_admin_deleted_hands_the_group_to_a_member(client, make_user, db_query):
    admin = make_user(role="admin")
    founder, member = make_user(), make_user()
    gid = client.post("/groups", headers=founder, json={"name": "Handover"}).json()["id"]
    client.post(f"/groups/{gid}/invitations", headers=founder, json={"user_id": me(client, member)["id"]})
    client.post(f"/invitations/{gid}/accept", headers=member)
    solo_gid = client.post("/groups", headers=founder, json={"name": "Solo"}).json()["id"]
    backdate_user(db_query, me(client, founder)["id"])

    run_now(client, admin)
    groups = {g["id"]: g for g in client.get("/groups", headers=member).json()}
    assert groups[gid]["role"] == "admin"                                        # handed over
    assert db_query("SELECT count(*) AS n FROM GroupSpace WHERE id = :id", id=solo_gid)[0]["n"] == 0


def test_inactive_group_is_deleted_with_everything_in_it(client, make_user, db_query):
    admin, owner = make_user(role="admin"), make_user()
    gid = client.post("/groups", headers=owner, json={"name": "Idle group"}).json()["id"]
    ingest_text(client, {**owner, "X-Space": gid}, title="Old", content="We decided long ago.")
    backdate_group(db_query, gid)

    status = client.get("/admin/retention", headers=admin).json()
    assert gid in {g["id"] for g in status["due"]["groups"]}
    report = run_now(client, admin)
    assert "Idle group" in report["groups_deleted"]
    assert db_query("SELECT count(*) AS n FROM Node WHERE user_id = :g", g=gid)[0]["n"] == 0
    assert client.get("/knowledge", headers={**owner, "X-Space": gid}).status_code == 403
    assert me(client, owner)["id"]                                               # the owner was active


def test_recent_activity_is_never_deleted_and_upcoming_is_listed(client, make_user, db_query):
    admin, soon = make_user(role="admin"), make_user()
    backdate_user(db_query, me(client, soon)["id"], SOON)
    status = client.get("/admin/retention", headers=admin).json()
    assert me(client, soon)["username"] in {u["username"] for u in status["upcoming"]["users"]}
    run_now(client, admin)
    assert me(client, soon)["id"]
    assert client.get("/admin/retention", headers=admin).json()["last_run"]["inactive_days"] == 365


def test_only_server_admins_see_or_run_retention(client, alice):
    assert client.get("/admin/retention", headers=alice).status_code == 403
    assert client.post("/admin/retention/run", headers=alice).status_code == 403


def test_last_admin_survives_and_existing_data_gets_a_fresh_clock(client):
    """Isolated database: these rules depend on who else exists."""
    from app import migrations
    from app.arcadedb import ArcadeClient, ArcadeSession
    from app.schema import apply_schema
    from app.services import retention
    from tests.conftest import ARCADEDB_AUTH, ARCADEDB_URL

    name = f"kh_test_ret_{uuid.uuid4().hex[:6]}"

    async def go():
        db = ArcadeClient(ARCADEDB_URL, name, *ARCADEDB_AUTH)
        try:
            await db.ensure_database()
            await apply_schema(db, 64, "fake:bow")
            for uid, username, role in [("a1", "only_admin", "admin"), ("m1", "old_member", "member")]:
                await db.command("INSERT INTO User CONTENT :d", {"d": {
                    "id": uid, "username": username, "username_key": username, "role": role, "created_at": LONG_AGO}})
            session = ArcadeSession(db)
            # upgrade: activity was never measured, so nobody may be deleted for it
            await migrations.start_activity_tracking(session)
            first = await retention.run(session)
            # a year passes for real
            await db.command("UPDATE User SET last_active_at = :t", {"t": LONG_AGO})
            second = await retention.run(session)
            again = await migrations.start_activity_tracking(session)     # runs once only
            left = [r["username"] for r in await db.command("SELECT username FROM User")]
            await session.close()
            return first, second, again, left
        finally:
            await db.server_command(f"drop database {name}")
            await db.aclose()

    first, second, again, left = client.portal.call(go)
    assert first["users_deleted"] == []
    assert second["users_deleted"] == ["old_member"]
    assert second["skipped"] == [{"user": "only_admin", "reason": "the last server admin is never deleted"}]
    assert again == 0 and left == ["only_admin"]
