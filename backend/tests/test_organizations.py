"""Organizations: the tenant. Roles, invite links, the shared space, groups inside, removal and deletion."""
from __future__ import annotations

import asyncio
import uuid
from typing import Dict

import pytest

from tests.helpers import create_group, create_org, ingest_text


def me(client, headers) -> Dict:
    return client.get("/auth/me", headers=headers).json()


def in_space(headers, space_id):
    return {**headers, "X-Space": space_id}


def link(client, headers, org_id, **body) -> Dict:
    r = client.post(f"/organizations/{org_id}/invites", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def join(client, headers, token):
    return client.post(f"/organizations/join/{token}", headers=headers)


@pytest.fixture
def acme(client, alice, bob) -> str:
    """alice owns Acme, bob is a member."""
    return create_org(client, alice, bob)


# ── creating and the shared space ────────────────────────────────────────────

def test_anyone_creates_an_organization_and_owns_it(client, alice):
    r = client.post("/organizations", headers=alice, json={"name": "  Acme  "})
    assert r.status_code == 201
    org = r.json()
    assert org["id"].startswith("org_") and org["name"] == "Acme" and org["role"] == "owner" and org["member_count"] == 1
    assert client.get("/organizations", headers=alice).json()[0]["id"] == org["id"]
    assert {"id": org["id"], "kind": "organization", "name": "Acme", "role": "owner", "member_count": 1} \
        in client.get("/spaces", headers=alice).json()


def test_members_share_the_organization_space(client, alice, bob, carol_headers, acme):
    ingest_text(client, in_space(alice, acme), title="Company decisions", content="We decided to use OKRs.")
    titles = {a["title"] for a in client.get("/knowledge", headers=in_space(bob, acme)).json()["artifacts"]}
    assert titles == {"Company decisions"}
    assert "Company decisions" not in {a["title"] for a in client.get("/knowledge", headers=alice).json()["artifacts"]}
    # outsiders can't enter it, or learn it exists
    assert client.get("/knowledge", headers=in_space(carol_headers, acme)).status_code == 403
    assert client.get(f"/organizations/{acme}", headers=carol_headers).status_code == 404
    assert client.get(f"/organizations/{acme}/members", headers=carol_headers).status_code == 404


# ── invite links ────────────────────────────────────────────────────────────

def test_invite_link_preview_and_join(client, alice, bob, db_query):
    org = create_org(client, alice)
    invite = link(client, alice, org)
    assert invite["role"] == "member" and invite["uses"] == 0 and len(invite["token"]) >= 40

    preview = client.get(f"/organizations/join/{invite['token']}", headers=bob).json()
    assert preview == {"org_id": org, "org_name": "Acme", "role": "member", "member_count": 1, "already_member": False}
    assert client.get(f"/organizations/join/{invite['token']}").status_code == 401     # signed in only

    r = join(client, bob, invite["token"])
    assert r.status_code == 200 and r.json()["role"] == "member" and r.json()["member_count"] == 2
    assert join(client, bob, invite["token"]).status_code == 200                     # again: no change
    listed = client.get(f"/organizations/{org}/invites", headers=alice).json()
    assert [(i["id"], i["uses"], i["usable"]) for i in listed] == [(invite["id"], 1, True)]
    # the token is shown once: never listed, and only its hash is stored
    assert "token" not in listed[0]
    stored = db_query("SELECT FROM OrgInvite WHERE id = :id", id=invite["id"])[0]
    assert invite["token"] not in str(stored) and stored["token_hash"]


def test_used_up_expired_revoked_and_made_up_links_are_refused(client, alice, make_user, db_query):
    org = create_org(client, alice)
    once = link(client, alice, org, max_uses=1)
    assert join(client, make_user(), once["token"]).status_code == 200
    assert join(client, make_user(), once["token"]).status_code == 404

    expired = link(client, alice, org)
    db_query("UPDATE OrgInvite SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = :id", id=expired["id"])
    assert join(client, make_user(), expired["token"]).status_code == 404

    revoked = link(client, alice, org)
    assert client.delete(f"/organizations/{org}/invites/{revoked['id']}", headers=alice).status_code == 204
    assert join(client, make_user(), revoked["token"]).status_code == 404
    assert revoked["id"] not in {i["id"] for i in client.get(f"/organizations/{org}/invites", headers=alice).json()}

    assert join(client, make_user(), "made-up-token").status_code == 404


@pytest.mark.parametrize("body", [{"expires_in_days": 0}, {"expires_in_days": 31}, {"max_uses": 0},
                                  {"role": "owner"}])
def test_invite_link_limits(client, alice, acme, body):
    assert client.post(f"/organizations/{acme}/invites", headers=alice, json=body).status_code == 422


# ── roles ───────────────────────────────────────────────────────────────────

def test_members_cannot_manage_the_organization(client, alice, bob, acme):
    alice_id = me(client, alice)["id"]
    assert client.post(f"/organizations/{acme}/invites", headers=bob, json={}).status_code == 403
    assert client.get(f"/organizations/{acme}/invites", headers=bob).status_code == 403
    assert client.patch(f"/organizations/{acme}", headers=bob, json={"name": "Mine"}).status_code == 403
    assert client.delete(f"/organizations/{acme}/members/{alice_id}", headers=bob).status_code == 403
    assert client.patch(f"/organizations/{acme}/members/{alice_id}", headers=bob, json={"role": "member"}).status_code == 403
    assert client.delete(f"/organizations/{acme}", headers=bob).status_code == 403
    # but they see who's in it
    members = client.get(f"/organizations/{acme}/members", headers=bob).json()
    assert {(m["username"], m["role"]) for m in members} == {
        (me(client, alice)["username"], "owner"), (me(client, bob)["username"], "member")}


def test_admins_invite_and_remove_members_but_not_admins_or_owners(client, alice, bob, make_user, acme):
    bob_id = me(client, bob)["id"]
    assert client.patch(f"/organizations/{acme}/members/{bob_id}", headers=alice, json={"role": "admin"}).status_code == 200
    # an admin invites members, not admins
    assert client.post(f"/organizations/{acme}/invites", headers=bob, json={"role": "admin"}).status_code == 403
    dave = make_user()
    assert join(client, dave, link(client, bob, acme)["token"]).json()["role"] == "member"
    assert client.patch(f"/organizations/{acme}", headers=bob, json={"name": "Acme Inc"}).json()["name"] == "Acme Inc"
    assert client.delete(f"/organizations/{acme}/members/{me(client, dave)['id']}", headers=bob).status_code == 204
    assert client.delete(f"/organizations/{acme}/members/{me(client, alice)['id']}", headers=bob).status_code == 403
    # owners can invite admins
    erin = make_user()
    assert join(client, erin, link(client, alice, acme, role="admin")["token"]).json()["role"] == "admin"


def test_an_organization_always_keeps_an_owner(client, alice, bob, acme):
    alice_id, bob_id = me(client, alice)["id"], me(client, bob)["id"]
    assert client.patch(f"/organizations/{acme}/members/{alice_id}", headers=alice, json={"role": "admin"}).status_code == 409
    assert client.post(f"/organizations/{acme}/leave", headers=alice).status_code == 409
    assert client.delete(f"/organizations/{acme}/members/{alice_id}", headers=alice).status_code == 400   # use leave
    # with a second owner, the first can step down and leave
    client.patch(f"/organizations/{acme}/members/{bob_id}", headers=alice, json={"role": "owner"})
    assert client.post(f"/organizations/{acme}/leave", headers=alice).status_code == 204
    assert acme not in {s["id"] for s in client.get("/spaces", headers=alice).json()}
    assert client.get(f"/organizations/{acme}", headers=bob).json()["member_count"] == 1


# ── groups inside ───────────────────────────────────────────────────────────

def test_groups_live_inside_an_organization(client, alice, bob, carol_headers, acme):
    # only its members can create one, and only invite its members
    assert client.post("/groups", headers=carol_headers, json={"name": "X", "org_id": acme}).status_code == 404
    assert client.post("/groups", headers=alice, json={"name": "X"}).status_code == 422
    group = create_group(client, alice, acme)
    carol = me(client, carol_headers)
    assert client.get(f"/groups/{group['id']}/candidates", headers=alice, params={"q": carol["username"]}).json() == []
    r = client.post(f"/groups/{group['id']}/invitations", headers=alice, json={"user_id": carol["id"]})
    assert r.status_code == 404
    bob_name = me(client, bob)["username"]
    found = client.get(f"/groups/{group['id']}/candidates", headers=alice, params={"q": bob_name}).json()
    assert [u["username"] for u in found] == [bob_name]


def test_leaving_the_organization_ends_its_group_memberships(client, alice, bob, acme):
    group = create_group(client, bob, acme, "Bob's team")
    ingest_text(client, in_space(bob, group["id"]), title="Team work", content="We decided to pair on reviews.")
    client.post(f"/groups/{group['id']}/invitations", headers=bob, json={"user_id": me(client, alice)["id"]})
    client.post(f"/invitations/{group['id']}/accept", headers=alice)

    assert client.post(f"/organizations/{acme}/leave", headers=bob).status_code == 204
    assert client.get("/knowledge", headers=in_space(bob, group["id"])).status_code == 403
    assert client.get("/knowledge", headers=in_space(bob, acme)).status_code == 403
    # the group and its work stay with the organization; its admin role passes on
    mine = {g["id"]: g for g in client.get("/groups", headers=alice).json()}
    assert mine[group["id"]]["role"] == "admin"
    assert "Team work" in {a["title"] for a in client.get("/knowledge", headers=in_space(alice, group["id"])).json()["artifacts"]}


def test_a_group_nobody_is_left_in_passes_to_an_owner(client, alice, bob, acme):
    group = create_group(client, bob, acme, "Solo")
    assert client.delete(f"/organizations/{acme}/members/{me(client, bob)['id']}", headers=alice).status_code == 204
    assert {g["id"]: g["role"] for g in client.get("/groups", headers=alice).json()}[group["id"]] == "admin"


def test_removed_members_need_a_new_link(client, alice, bob, acme):
    old = link(client, alice, acme)
    assert client.delete(f"/organizations/{acme}/members/{me(client, bob)['id']}", headers=alice).status_code == 204
    assert client.get("/knowledge", headers=in_space(bob, acme)).status_code == 403
    assert join(client, bob, old["token"]).status_code == 403
    assert join(client, bob, link(client, alice, acme)["token"]).status_code == 200
    assert client.get("/knowledge", headers=in_space(bob, acme)).status_code == 200


def test_people_who_left_can_rejoin_with_a_link(client, alice, bob, acme):
    token = link(client, alice, acme)["token"]
    client.post(f"/organizations/{acme}/leave", headers=bob)
    assert join(client, bob, token).status_code == 200


# ── deleting ────────────────────────────────────────────────────────────────

def test_deleting_an_organization_deletes_everything_in_it(client, alice, bob, acme, db_query):
    group = create_group(client, alice, acme)
    ingest_text(client, in_space(bob, acme), title="Org doc", content="We decided org things.")
    ingest_text(client, in_space(alice, group["id"]), title="Group doc", content="We decided group things.")
    link(client, alice, acme)
    assert client.delete(f"/organizations/{acme}", headers=bob).status_code == 403
    assert client.delete(f"/organizations/{acme}", headers=alice).status_code == 204
    for space in (acme, group["id"]):
        assert db_query("SELECT count(*) AS n FROM Node WHERE user_id = :s", s=space)[0]["n"] == 0
        assert client.get("/knowledge", headers=in_space(bob, space)).status_code == 403
    for doc in ("Organization", "GroupSpace"):
        assert db_query(f"SELECT count(*) AS n FROM {doc} WHERE id IN :ids", ids=[acme, group["id"]])[0]["n"] == 0
    for doc in ("OrgMember", "OrgInvite"):
        assert db_query(f"SELECT count(*) AS n FROM {doc} WHERE org_id = :o", o=acme)[0]["n"] == 0


def test_deleting_an_inactive_sole_owner(client, make_user, db_query):
    admin, founder, member, loner = make_user(role="admin"), make_user(), make_user(), make_user()
    shared = create_org(client, founder, member, name="Shared")
    solo = create_org(client, loner, name="Solo")
    ingest_text(client, in_space(loner, solo), title="Solo doc", content="We decided alone.")
    for h in (founder, loner):
        uid = me(client, h)["id"]
        db_query("UPDATE User SET last_active_at = :t, last_login_at = :t, created_at = :t WHERE id = :id",
                 t="2020-01-01T00:00:00+00:00", id=uid)
    report = client.post("/admin/retention/run", headers=admin).json()
    assert {o["id"]: o["role"] for o in client.get("/organizations", headers=member).json()}[shared] == "owner"
    assert db_query("SELECT count(*) AS n FROM Organization WHERE id = :o", o=solo)[0]["n"] == 0
    assert db_query("SELECT count(*) AS n FROM Node WHERE user_id = :o", o=solo)[0]["n"] == 0
    assert report["users_deleted"]


# ── upgrading ───────────────────────────────────────────────────────────────

def test_upgrade_moves_existing_accounts_and_groups_into_one_organization(client, make_user, db_query):
    from app.db import client_from_env
    from app.migrations import migrate_organizations
    from app.arcadedb import ArcadeSession

    admin, member = make_user(role="admin"), make_user()
    legacy = f"grp_{uuid.uuid4().hex[:16]}"
    db_query("INSERT INTO GroupSpace CONTENT :doc", doc={"id": legacy, "name": "Legacy", "created_by": me(client, admin)["id"],
                                                         "created_at": "2024-01-01T00:00:00+00:00"})
    db_query("DELETE FROM Meta WHERE key = 'organizations_v1'")

    async def migrate():
        arcade = client_from_env()
        try:
            return await migrate_organizations(ArcadeSession(arcade))
        finally:
            await arcade.aclose()
    try:
        assert asyncio.run(migrate()) >= 2
        roles = {o["id"]: o["role"] for o in client.get("/organizations", headers=admin).json()}
        assert roles.get("org_default") == "owner"
        assert {o["id"]: o["role"] for o in client.get("/organizations", headers=member).json()}["org_default"] == "member"
        assert db_query("SELECT org_id FROM GroupSpace WHERE id = :g", g=legacy)[0]["org_id"] == "org_default"
        assert asyncio.run(migrate()) == 0                                           # once only
    finally:
        # other tests expect accounts to start with no organization
        db_query("DELETE FROM OrgMember WHERE org_id = 'org_default'")
        db_query("DELETE FROM Organization WHERE id = 'org_default'")
        db_query("DELETE FROM GroupSpace WHERE id = :g", g=legacy)
