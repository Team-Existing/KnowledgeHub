"""Groups and spaces: any user creates a group and is its admin; invited members share everything in it."""
from __future__ import annotations

from typing import Dict

import pytest

from tests.helpers import ingest_text


def in_space(headers: Dict[str, str], space_id: str) -> Dict[str, str]:
    return {**headers, "X-Space": space_id}


def me(client, headers) -> Dict:
    return client.get("/auth/me", headers=headers).json()


def create_group(client, headers, name="Platform team") -> Dict:
    r = client.post("/groups", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def invite(client, admin, group_id, user_headers) -> None:
    r = client.post(f"/groups/{group_id}/invitations", headers=admin, json={"user_id": me(client, user_headers)["id"]})
    assert r.status_code == 201, r.text


def join(client, admin, group_id, user_headers) -> None:
    invite(client, admin, group_id, user_headers)
    assert client.post(f"/invitations/{group_id}/accept", headers=user_headers).status_code == 200


@pytest.fixture
def team(client, alice, bob):
    """alice creates a group (so she is its admin) and bob joins it."""
    group = create_group(client, alice)
    join(client, alice, group["id"], bob)
    return group


# ── creating groups and spaces ──────────────────────────────────────────────

def test_any_user_can_create_a_group_and_becomes_its_admin(client, alice):
    assert me(client, alice)["role"] == "member"          # a normal account, not a server admin
    group = create_group(client, alice)
    assert group["role"] == "admin" and group["member_count"] == 1
    spaces = client.get("/spaces", headers=alice).json()
    assert spaces[0] == {"id": me(client, alice)["id"], "kind": "personal", "name": "My space", "role": "owner"}
    assert {"id": group["id"], "kind": "group", "name": "Platform team", "role": "admin", "member_count": 1} in spaces


# ── inviting ────────────────────────────────────────────────────────────────

def test_admin_searches_and_invites_and_the_user_accepts(client, alice, bob):
    group = create_group(client, alice)
    bob_name = me(client, bob)["username"]
    found = client.get(f"/groups/{group['id']}/candidates", headers=alice, params={"q": bob_name[-6:]}).json()
    assert [u["username"] for u in found] == [bob_name] and found[0]["status"] is None

    invite(client, alice, group["id"], bob)
    found = client.get(f"/groups/{group['id']}/candidates", headers=alice, params={"q": bob_name}).json()
    assert found[0]["status"] == "invited"
    invitations = client.get("/invitations", headers=bob).json()
    assert [(i["group_id"], i["invited_by"]) for i in invitations] == [(group["id"], me(client, alice)["username"])]

    # not a member until accepted
    assert client.get("/knowledge", headers=in_space(bob, group["id"])).status_code == 403
    assert group["id"] not in {s["id"] for s in client.get("/spaces", headers=bob).json()}

    assert client.post(f"/invitations/{group['id']}/accept", headers=bob).status_code == 200
    assert client.get("/knowledge", headers=in_space(bob, group["id"])).status_code == 200
    assert client.get("/invitations", headers=bob).json() == []
    members = client.get(f"/groups/{group['id']}/members", headers=alice).json()
    assert {(m["username"], m["role"], m["status"]) for m in members} == {
        (me(client, alice)["username"], "admin", "active"), (bob_name, "member", "active")}


def test_only_the_group_admin_manages_membership(client, alice, bob, carol_headers, team):
    gid = team["id"]
    carol_id = me(client, carol_headers)["id"]
    assert client.get(f"/groups/{gid}/candidates", headers=bob, params={"q": "user"}).status_code == 403
    assert client.post(f"/groups/{gid}/invitations", headers=bob, json={"user_id": carol_id}).status_code == 403
    assert client.delete(f"/groups/{gid}/members/{me(client, alice)['id']}", headers=bob).status_code == 403
    assert client.patch(f"/groups/{gid}", headers=bob, json={"name": "mine now"}).status_code == 403
    assert client.delete(f"/groups/{gid}", headers=bob).status_code == 403
    # outsiders don't learn the group exists
    assert client.get(f"/groups/{gid}/members", headers=carol_headers).status_code == 404


def test_decline_reinvite_duplicate_and_unknown_user(client, alice, bob):
    gid = create_group(client, alice)["id"]
    invite(client, alice, gid, bob)
    r = client.post(f"/groups/{gid}/invitations", headers=alice, json={"user_id": me(client, bob)["id"]})
    assert r.status_code == 409
    assert client.post(f"/invitations/{gid}/decline", headers=bob).status_code == 204
    assert client.post(f"/invitations/{gid}/accept", headers=bob).status_code == 404     # nothing pending now
    invite(client, alice, gid, bob)                                                      # can be invited again
    assert client.post(f"/groups/{gid}/invitations", headers=alice, json={"user_id": "nobody"}).status_code == 404


# ── sharing inside the group ────────────────────────────────────────────────

def test_members_share_the_group_space_but_personal_spaces_stay_private(client, alice, bob, carol_headers, team):
    gid = team["id"]
    shared = ingest_text(client, in_space(alice, gid), title="Team decisions",
                         content="We decided to adopt trunk-based development.")
    personal = ingest_text(client, alice, title="Alice private notes", content="We decided to learn Rust.")

    bob_group = client.get("/knowledge", headers=in_space(bob, gid)).json()
    assert {a["title"] for a in bob_group["artifacts"]} == {"Team decisions"}
    # bob works on it like his own: reviews an item alice's ingest produced
    item = shared["items"][0]
    r = client.patch(f"/knowledge/review/{item['id']}", headers=in_space(bob, gid), json={"status": "accepted"})
    assert r.status_code == 200
    assert client.get(f"/knowledge/items/{item['id']}", headers=in_space(alice, gid)).json()["review_status"] == "accepted"

    # nothing leaks across spaces
    assert "Team decisions" not in {a["title"] for a in client.get("/knowledge", headers=alice).json()["artifacts"]}
    assert client.get(f"/knowledge/items/{personal['items'][0]['id']}", headers=in_space(bob, gid)).status_code == 404
    assert client.get(f"/knowledge/items/{item['id']}", headers=bob).status_code == 404       # bob's personal space
    assert client.get("/knowledge", headers=in_space(carol_headers, gid)).status_code == 403


def test_group_history_records_who_did_what(client, alice, bob, team):
    gid = team["id"]
    item = ingest_text(client, in_space(alice, gid), title="Ops",
                       content="We decided to page on-call for P1 only.")["items"][0]
    client.patch(f"/knowledge/review/{item['id']}", headers=in_space(bob, gid), json={"status": "accepted"})
    client.put(f"/knowledge/items/{item['id']}", headers=in_space(alice, gid), json={"title": "Page on-call for P1"})
    events = client.get(f"/knowledge/items/{item['id']}/lineage", headers=in_space(alice, gid)).json()["events"]
    assert [(e["kind"], e["actor"]) for e in events] == [
        ("reviewed", me(client, bob)["username"]), ("edited", me(client, alice)["username"])]


def test_lineage_search_and_graphrag_work_inside_the_group(client, alice, bob, team, llm):
    gid = team["id"]
    old = ingest_text(client, in_space(alice, gid), title="2023", content="We decided to adopt MySQL for billing.")
    new = ingest_text(client, in_space(bob, gid), title="2025",
                      content="We decided to move billing to PostgreSQL for JSON support.")
    old_id = next(i["id"] for i in old["items"] if i["type"] == "decision")
    new_id = next(i["id"] for i in new["items"] if i["type"] == "decision")
    r = client.post(f"/knowledge/items/{new_id}/lineage", headers=in_space(bob, gid),
                    json={"target_id": old_id, "kind": "supersedes"})
    assert r.status_code == 201
    assert client.get(f"/knowledge/items/{old_id}", headers=in_space(alice, gid)).json()["status"] == "superseded"
    hits = client.get("/knowledge/search", headers=in_space(alice, gid), params={"q": "billing"}).json()
    assert {i["id"] for i in hits["knowledge_items"]} >= {old_id, new_id}

    llm.handlers["generate"] = lambda messages: f"PostgreSQL [{new_id}]"
    r = client.post("/knowledge/graphrag/query", headers=in_space(alice, gid), json={"question": "billing database?"})
    assert r.status_code == 200 and new_id in llm.last("generate")[-1]["content"]


def test_connectors_belong_to_the_group(client, alice, bob, team):
    gid = team["id"]
    r = client.post("/connectors", headers=in_space(alice, gid), json={
        "kind": "github", "name": "Team repo", "config": {"repo_url": "https://github.com/acme/api"}})
    assert r.status_code == 201
    assert [c["name"] for c in client.get("/connectors", headers=in_space(bob, gid)).json()] == ["Team repo"]
    assert client.get("/connectors", headers=bob).json() == []


def test_sharing_a_source_from_a_personal_space_into_the_group(client, alice, bob, carol_headers, team):
    gid = team["id"]
    mine = ingest_text(client, alice, title="Retro notes",
                       content="Lesson learned: canary releases caught the bad config. We decided to keep canaries.")
    first = mine["items"][0]
    client.patch(f"/knowledge/review/{first['id']}", headers=alice, json={"status": "accepted"})

    r = client.post(f"/knowledge/artifacts/{mine['artifact']['id']}/share", headers=alice, json={"space_id": gid})
    assert r.status_code == 200 and r.json()["items"] == len(mine["items"])
    group_view = client.get("/knowledge", headers=in_space(bob, gid)).json()
    copy = next(a for a in group_view["artifacts"] if a["title"] == "Retro notes")
    assert copy["metadata"]["shared_from"]["by"] == me(client, alice)["username"]
    copied = [i for i in group_view["knowledge_items"] if i["artifact_id"] == copy["id"]]
    assert len(copied) == len(mine["items"])
    assert any(i["title"] == first["title"] and i["review_status"] == "accepted" for i in copied)
    # sharing again updates the copy instead of duplicating it
    client.post(f"/knowledge/artifacts/{mine['artifact']['id']}/share", headers=alice, json={"space_id": gid})
    assert sum(a["title"] == "Retro notes" for a in client.get("/knowledge", headers=in_space(bob, gid)).json()["artifacts"]) == 1
    # only into spaces you belong to
    r = client.post(f"/knowledge/artifacts/{mine['artifact']['id']}/share", headers=alice,
                    json={"space_id": me(client, carol_headers)["id"]})
    assert r.status_code == 403


# ── leaving, removal, deletion ──────────────────────────────────────────────

def test_removed_or_departed_members_lose_access_but_their_work_stays(client, alice, bob, carol_headers, team):
    gid = team["id"]
    ingest_text(client, in_space(bob, gid), title="Bob's contribution", content="We decided to add SLOs.")
    assert client.delete(f"/groups/{gid}/members/{me(client, bob)['id']}", headers=alice).status_code == 204
    assert client.get("/knowledge", headers=in_space(bob, gid)).status_code == 403
    assert "Bob's contribution" in {a["title"] for a in client.get("/knowledge", headers=in_space(alice, gid)).json()["artifacts"]}

    join(client, alice, gid, carol_headers)
    assert client.post(f"/groups/{gid}/leave", headers=carol_headers).status_code == 204
    assert gid not in {s["id"] for s in client.get("/spaces", headers=carol_headers).json()}
    # the admin can't walk away from the group
    assert client.post(f"/groups/{gid}/leave", headers=alice).status_code == 400
    assert client.delete(f"/groups/{gid}/members/{me(client, alice)['id']}", headers=alice).status_code == 400


def test_deleting_a_group_deletes_its_space_for_everyone(client, alice, bob, team, db_query):
    gid = team["id"]
    ingest_text(client, in_space(bob, gid), title="Doomed", content="We decided this will be deleted.")
    assert client.delete(f"/groups/{gid}", headers=alice).status_code == 204
    assert client.get("/knowledge", headers=in_space(bob, gid)).status_code == 403
    assert db_query("SELECT count(*) AS n FROM Node WHERE user_id = :g", g=gid)[0]["n"] == 0
    assert db_query("SELECT count(*) AS n FROM Membership WHERE group_id = :g", g=gid)[0]["n"] == 0


def test_a_user_id_is_not_a_space_you_can_enter(client, alice, bob):
    """X-Space must be your own id or a group you're in — never another user's personal space."""
    ingest_text(client, bob, title="Bob only", content="We decided bob's secret.")
    assert client.get("/knowledge", headers=in_space(alice, me(client, bob)["id"])).status_code == 403
