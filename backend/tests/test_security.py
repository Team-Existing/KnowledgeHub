"""Role-based access control, signing-key enforcement, credential encryption and the security migrations."""
from __future__ import annotations

import uuid

import httpx
import pytest

from app import crypto
from tests.conftest import ARCADEDB_AUTH, ARCADEDB_URL

LINEAR = "https://api.linear.app/graphql"


def register(client, username=None, password="secret123"):
    username = username or f"user_{uuid.uuid4().hex[:10]}"
    r = client.post("/auth/register", json={"username": username, "password": password})
    return r, username


def headers_for(client, username, password="secret123"):
    r = client.post("/auth/token", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── signing key ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["", "change-me-in-production-use-32-chars-min", "short-but-random-Xy9"])
def test_server_refuses_weak_signing_keys(monkeypatch, key):
    import app.auth as auth
    monkeypatch.setattr(auth, "SECRET_KEY", key)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        auth.check_secret_key()


def test_tokens_signed_with_another_key_are_rejected(client, make_user):
    from jose import jwt
    from app.auth import ALGORITHM, SECRET_KEY
    headers = make_user(role="admin")
    sub = jwt.decode(headers["Authorization"].split()[1], SECRET_KEY, algorithms=[ALGORITHM])["sub"]
    forged = jwt.encode({"sub": sub}, "change-me-in-production-use-32-chars-min", algorithm=ALGORITHM)
    assert client.get("/admin/users", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


# ── roles ───────────────────────────────────────────────────────────────────

def test_only_the_first_account_becomes_admin(client, db_query):
    # pretend this is a fresh install: release the "first admin" claim the session made
    claim = db_query("SELECT value FROM Meta WHERE key = 'first_admin'")[0]["value"]
    db_query("DELETE FROM Meta WHERE key = 'first_admin'")
    try:
        first, _ = register(client)
        second, _ = register(client)
        assert first.json()["role"] == "admin" and second.json()["role"] == "member"
    finally:
        db_query("UPDATE Meta SET value = :v WHERE key = 'first_admin'", v=claim)


def test_me_reports_role_and_permissions(client, make_user):
    member = client.get("/auth/me", headers=make_user()).json()
    admin = client.get("/auth/me", headers=make_user(role="admin")).json()
    assert member["role"] == "member" and member["permissions"] == []
    assert admin["role"] == "admin"
    assert set(admin["permissions"]) == {"manage_users", "manage_models", "use_filesystem_connectors"}


def test_members_cannot_administer(client, make_user):
    member = make_user()
    assert client.get("/admin/users", headers=member).status_code == 403
    assert client.post("/admin/users", headers=member,
                       json={"username": "x_" + uuid.uuid4().hex[:6], "password": "secret123", "role": "admin"}
                       ).status_code == 403
    me = client.get("/auth/me", headers=member).json()
    assert client.patch(f"/admin/users/{me['id']}", headers=member, json={"role": "admin"}).status_code == 403


def test_admin_manages_roles_and_changes_apply_immediately(client, make_user, ollama):
    admin = make_user(role="admin")
    _, username = register(client)
    member = headers_for(client, username)
    user_id = client.get("/auth/me", headers=member).json()["id"]
    ollama.installed = {"llama3.1:8b", "mistral:7b"}
    assert client.post("/models/remove", headers=member, json={"model_id": "mistral:7b"}).status_code == 403

    users = {u["id"]: u for u in client.get("/admin/users", headers=admin).json()}
    assert users[user_id]["role"] == "member" and "hashed_password" not in users[user_id]
    r = client.patch(f"/admin/users/{user_id}", headers=admin, json={"role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    # same token, new role: roles are read from the database on every request
    assert client.post("/models/remove", headers=member, json={"model_id": "mistral:7b"}).status_code == 200
    assert client.patch(f"/admin/users/{user_id}", headers=admin, json={"role": "owner"}).status_code == 422


def test_last_admin_cannot_be_demoted(client, db_query, make_user):
    admin = make_user(role="admin")
    me = client.get("/auth/me", headers=admin).json()
    others = db_query("SELECT id FROM User WHERE role = 'admin' AND id <> :id", id=me["id"])
    db_query("UPDATE User SET role = 'member' WHERE role = 'admin' AND id <> :id", id=me["id"])
    try:
        r = client.patch(f"/admin/users/{me['id']}", headers=admin, json={"role": "member"})
        assert r.status_code == 409
    finally:
        for o in others:
            db_query("UPDATE User SET role = 'admin' WHERE id = :id", id=o["id"])


def test_unknown_role_in_database_grants_nothing(client, make_user, db_query):
    headers = make_user()
    me = client.get("/auth/me", headers=headers).json()
    db_query("UPDATE User SET role = 'superuser' WHERE id = :id", id=me["id"])
    assert client.get("/admin/users", headers=headers).status_code == 403
    assert client.get("/auth/me", headers=headers).json()["permissions"] == []


def test_closed_registration_admins_create_accounts(client, make_user, monkeypatch):
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    r, _ = register(client)
    assert r.status_code == 403
    monkeypatch.setenv("ALLOW_REGISTRATION", "true")   # make_user registers; reopen just for setup
    admin_headers = make_user(role="admin")
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    username = "made_" + uuid.uuid4().hex[:6]
    r = client.post("/admin/users", headers=admin_headers, json={"username": username, "password": "secret123"})
    assert r.status_code == 201 and r.json()["role"] == "member"
    assert client.get("/auth/me", headers=headers_for(client, username)).json()["username"] == username


# ── encryption primitives (no database) ─────────────────────────────────────

def test_encryption_binds_value_to_its_record(monkeypatch):
    monkeypatch.setenv("CREDENTIALS_KEY", crypto.generate_key())
    sealed = crypto.encrypt("ghp_real_token", "connector:a:token")
    assert crypto.is_encrypted(sealed) and "ghp_real_token" not in sealed
    assert crypto.encrypt("ghp_real_token", "connector:a:token") != sealed      # random nonce
    assert crypto.decrypt(sealed, "connector:a:token") == "ghp_real_token"
    for wrong in ("connector:b:token", "connector:a:api_key"):
        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(sealed, wrong)
    tampered = sealed[:-6] + ("AAAAAA" if not sealed.endswith("AAAAAA") else "BBBBBB")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(tampered, "connector:a:token")


def test_key_rotation(monkeypatch):
    old, new = crypto.generate_key(), crypto.generate_key()
    monkeypatch.setenv("CREDENTIALS_KEY", old)
    sealed = crypto.encrypt("secret", "ctx")
    monkeypatch.setenv("CREDENTIALS_KEY", f"{new},{old}")
    assert crypto.decrypt(sealed, "ctx") == "secret" and crypto.needs_reencryption(sealed)
    resealed = crypto.encrypt(crypto.decrypt(sealed, "ctx"), "ctx")
    assert not crypto.needs_reencryption(resealed)
    monkeypatch.setenv("CREDENTIALS_KEY", new)
    assert crypto.decrypt(resealed, "ctx") == "secret"
    with pytest.raises(crypto.DecryptionError, match="not in CREDENTIALS_KEY"):
        crypto.decrypt(sealed, "ctx")


def test_no_key_means_no_storage(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_KEY", raising=False)
    with pytest.raises(crypto.CredentialsKeyMissing):
        crypto.encrypt("x", "ctx")
    monkeypatch.setenv("CREDENTIALS_KEY", "too-short")
    assert not crypto.available()


# ── stored connector credentials ────────────────────────────────────────────

def _stored_config(db_query, connector_id):
    return db_query("SELECT config FROM Connector WHERE id = :id", id=connector_id)[0]["config"]


def test_connector_secrets_are_encrypted_at_rest_and_never_returned(client, alice, db_query):
    r = client.post("/connectors", headers=alice, json={"kind": "linear", "name": "L",
                                                         "config": {"api_url": LINEAR, "api_key": "lin_api_TOPSECRET"}})
    assert r.status_code == 201
    conn = r.json()
    stored = _stored_config(db_query, conn["id"])
    assert crypto.is_encrypted(stored["api_key"]) and "TOPSECRET" not in str(stored)
    assert crypto.decrypt(stored["api_key"], f"connector:{conn['id']}:api_key") == "lin_api_TOPSECRET"
    for listing in (conn, *client.get("/connectors", headers=alice).json()):
        assert "TOPSECRET" not in str(listing)

    # PATCH with the mask keeps the ciphertext; a new value is re-encrypted
    client.patch(f"/connectors/{conn['id']}", headers=alice, json={"config": {"api_url": LINEAR, "api_key": "********"}})
    assert _stored_config(db_query, conn["id"])["api_key"] == stored["api_key"]
    client.patch(f"/connectors/{conn['id']}", headers=alice, json={"config": {"api_url": LINEAR, "api_key": "lin_api_NEW"}})
    rotated = _stored_config(db_query, conn["id"])["api_key"]
    assert rotated != stored["api_key"] and "NEW" not in rotated


def test_clients_cannot_plant_ciphertext(client, alice):
    other = crypto.encrypt("stolen", "connector:conn_someone_else:api_key")
    r = client.post("/connectors", headers=alice, json={"kind": "linear", "name": "L", "config": {"api_url": LINEAR, "api_key": other}})
    assert r.status_code == 400


def test_ciphertext_moved_between_connectors_is_refused(client, alice, db_query, mock_http):
    mock_http(lambda request: httpx.Response(200, json={"data": {"issues": {"nodes": [], "pageInfo": {}}}}))
    a = client.post("/connectors", headers=alice, json={"kind": "linear", "name": "A", "config": {"api_url": LINEAR, "api_key": "k1"}}).json()
    b = client.post("/connectors", headers=alice, json={"kind": "linear", "name": "B", "config": {"api_url": LINEAR, "api_key": "k2"}}).json()
    # an attacker with database access copies A's encrypted key into B
    moved = {**_stored_config(db_query, b["id"]), "api_key": _stored_config(db_query, a["id"])["api_key"]}
    db_query("UPDATE Connector SET config = :c WHERE id = :id", c=moved, id=b["id"])
    assert _stored_config(db_query, b["id"])["api_key"] == _stored_config(db_query, a["id"])["api_key"]
    client.post(f"/connectors/{b['id']}/sync", headers=alice)
    status = next(c for c in client.get("/connectors", headers=alice).json() if c["id"] == b["id"])
    assert status["last_status"] == "error" and "Re-enter them" in status["last_error"]


def test_storing_credentials_without_a_key_is_refused(client, alice, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_KEY")
    r = client.post("/connectors", headers=alice, json={"kind": "linear", "name": "L", "config": {"api_url": LINEAR, "api_key": "k"}})
    assert r.status_code == 503 and "CREDENTIALS_KEY" in r.json()["detail"]
    kinds = {k["kind"]: k for k in client.get("/connectors/kinds", headers=alice).json()}
    assert kinds["linear"]["needs_credentials_key"] is True
    # connectors without secrets are unaffected
    assert client.post("/connectors", headers=alice, json={"kind": "github", "name": "G",
                                                            "config": {"repo_url": "https://github.com/acme/api"}}).status_code == 201


# ── migrations ──────────────────────────────────────────────────────────────

def test_credentials_migration_encrypts_legacy_plaintext_and_rotates_keys(client, alice, db_query, monkeypatch):
    from app import migrations
    from app.arcadedb import ArcadeSession
    from app.db import get_client

    def run_migration():
        async def go():
            session = ArcadeSession(get_client())
            try:
                return await migrations.migrate_credentials(session)
            finally:
                await session.close()
        return client.portal.call(go)

    me = client.get("/auth/me", headers=alice).json()
    legacy_id = f"conn_legacy_{uuid.uuid4().hex[:8]}"
    db_query("INSERT INTO Connector CONTENT :doc", doc={
        "id": legacy_id, "user_id": me["id"], "kind": "jira", "name": "legacy",
        "config": {"base_url": "https://j.example", "api_token": "plain-legacy-token"}})
    assert run_migration() >= 1
    sealed = _stored_config(db_query, legacy_id)["api_token"]
    assert crypto.decrypt(sealed, f"connector:{legacy_id}:api_token") == "plain-legacy-token"

    old = __import__("os").environ["CREDENTIALS_KEY"]
    new = crypto.generate_key()
    monkeypatch.setenv("CREDENTIALS_KEY", f"{new},{old}")
    run_migration()
    monkeypatch.setenv("CREDENTIALS_KEY", new)   # old key retired
    resealed = _stored_config(db_query, legacy_id)["api_token"]
    assert crypto.decrypt(resealed, f"connector:{legacy_id}:api_token") == "plain-legacy-token"


def test_roles_migration_demotes_everyone_but_the_earliest_admin(client):
    """Runs against its own throwaway database: it rewrites every user's role."""
    from app import migrations
    from app.arcadedb import ArcadeClient, ArcadeSession
    from app.schema import apply_schema

    name = f"kh_test_mig_{uuid.uuid4().hex[:6]}"

    async def go():
        db = ArcadeClient(ARCADEDB_URL, name, *ARCADEDB_AUTH)
        try:
            await db.ensure_database()
            await apply_schema(db, 64, "fake:bow")
            for i, (username, created) in enumerate([("old_founder", "2024-01-01"), ("later", "2024-02-01"),
                                                     ("latest", "2024-03-01")]):
                await db.command("INSERT INTO User CONTENT :d", {"d": {
                    "id": f"u{i}", "username": username, "role": "admin", "created_at": created}})
            session = ArcadeSession(db)
            demoted = await migrations.migrate_roles(session)
            again = await migrations.migrate_roles(session)          # idempotent
            roles = {r["username"]: r["role"] for r in await db.command("SELECT username, role FROM User")}
            await session.close()
            return demoted, again, roles
        finally:
            await db.server_command(f"drop database {name}")
            await db.aclose()

    demoted, again, roles = client.portal.call(go)
    assert sorted(demoted) == ["later", "latest"] and again == []
    assert roles == {"old_founder": "admin", "later": "member", "latest": "member"}
