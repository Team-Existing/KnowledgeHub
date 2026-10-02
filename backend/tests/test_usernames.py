"""Usernames are unique ignoring case and Unicode form; new ones use a plain character set."""
from __future__ import annotations

import uuid

import pytest

from app.usernames import check_new_username, username_key


# ── rules (no database) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, display", [
    ("Alice", "Alice"), ("  alice.b-2 ", "alice.b-2"), ("A_1", "A_1"),
    ("Alｉce", "Alice"),            # full-width letters normalise to ASCII (NFKC)
])
def test_valid_usernames_keep_their_casing(raw, display):
    assert check_new_username(raw) == display


@pytest.mark.parametrize("raw", ["al", "ali ce", "аlice", "_alice", "alice!", "a" * 61, "élodie"])
def test_invalid_usernames_are_refused(raw):
    with pytest.raises(ValueError, match="Username must be"):
        check_new_username(raw)


def test_key_ignores_case_and_spacing():
    assert username_key("ALICE") == username_key(" alice ") == username_key("Alice") == "alice"


# ── API ─────────────────────────────────────────────────────────────────────

def _register(client, username, password="secret123"):
    return client.post("/auth/register", json={"username": username, "password": password})


def test_names_differing_only_in_case_or_spaces_are_duplicates(client):
    base = f"Dana_{uuid.uuid4().hex[:6]}"
    assert _register(client, base).status_code == 201
    for variant in (base, base.lower(), base.upper(), f"  {base}  "):
        r = _register(client, variant)
        assert r.status_code == 409 and r.json()["detail"] == "Username already taken", variant


def test_invalid_names_get_a_clear_422(client):
    r = _register(client, "has space")
    assert r.status_code == 422 and "Username must be" in str(r.json()["detail"])


def test_sign_in_is_case_insensitive_and_shows_the_chosen_casing(client):
    name = f"Eve_{uuid.uuid4().hex[:6]}"
    _register(client, name)
    r = client.post("/auth/token", data={"username": name.upper(), "password": "secret123"})
    assert r.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["username"] == name


def test_admin_created_accounts_follow_the_same_rule(client, make_user):
    admin = make_user(role="admin")
    name = f"Finn_{uuid.uuid4().hex[:6]}"
    assert client.post("/admin/users", headers=admin, json={"username": name, "password": "secret123"}).status_code == 201
    r = client.post("/admin/users", headers=admin, json={"username": name.lower(), "password": "secret123"})
    assert r.status_code == 409


def test_database_enforces_uniqueness_too(client, db_query):
    """Even a write that skips the API check is refused by the UNIQUE index on username_key."""
    name = f"gus_{uuid.uuid4().hex[:6]}"
    _register(client, name)
    import httpx
    from tests.conftest import ARCADEDB_AUTH, ARCADEDB_URL, TEST_DATABASE
    r = httpx.post(f"{ARCADEDB_URL}/api/v1/command/{TEST_DATABASE}", auth=ARCADEDB_AUTH, timeout=30, json={
        "language": "sql", "command": "INSERT INTO User CONTENT :d",
        "params": {"d": {"id": str(uuid.uuid4()), "username": name.upper(), "username_key": name}}})
    assert r.status_code >= 400 and "Duplicate" in r.text


def test_legacy_case_clashes_are_kept_reachable_and_reported(client):
    """Accounts that already differ only in case keep working; the index still gets created."""
    from app import migrations
    from app.arcadedb import ArcadeClient, ArcadeSession
    from app.schema import apply_schema
    from tests.conftest import ARCADEDB_AUTH, ARCADEDB_URL

    name = f"kh_test_names_{uuid.uuid4().hex[:6]}"

    async def go():
        db = ArcadeClient(ARCADEDB_URL, name, *ARCADEDB_AUTH)
        try:
            await db.ensure_database()
            await apply_schema(db, 64, "fake:bow")
            for i, (username, created) in enumerate([("Sam", "2024-01-01"), ("sam", "2024-02-01"), ("Kim", "2024-03-01")]):
                await db.command("INSERT INTO User CONTENT :d", {"d": {"id": f"u{i}abcdefgh", "username": username,
                                                                        "created_at": created}})
            session = ArcadeSession(db)
            clashes = await migrations.migrate_username_keys(session)
            from app.repositories.users import UserRepository
            repo = UserRepository(session)
            found = {n: (await repo.get_by_username(n))["id"] for n in ("Sam", "sam", "SAM", "kim")}
            keys = {r["username"]: r["username_key"] for r in await db.command("SELECT username, username_key FROM User")}
            await session.close()
            return clashes, found, keys
        finally:
            await db.server_command(f"drop database {name}")
            await db.aclose()

    clashes, found, keys = client.portal.call(go)
    assert clashes == ["sam (same as Sam)"]
    assert keys["Sam"] == "sam" and keys["sam"].startswith("sam~") and keys["Kim"] == "kim"
    # exact names reach their own account; other casings reach the older one
    assert found == {"Sam": "u0abcdefgh", "sam": "u1abcdefgh", "SAM": "u0abcdefgh", "kim": "u2abcdefgh"}
