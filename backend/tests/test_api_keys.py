"""Read-only API keys for developer portals: one space, read routes only, acting as a still-member creator."""
from __future__ import annotations

from tests.helpers import create_group, create_org, ingest_text


def key_headers(key):
    return {"Authorization": f"Bearer {key}"}


def new_key(client, headers, name="Backstage"):
    r = client.post("/knowledge/api-keys", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_key_reads_its_space_and_only_reads(client, alice, db_query):
    item = ingest_text(client, alice, title="ADR", content="We decided to use PostgreSQL for payments.")["items"][0]
    created = new_key(client, alice)
    key = created["key"]
    assert key.startswith("khk_") and created["hint"].startswith("khk_") and key not in created["hint"]
    assert key not in str(db_query("SELECT FROM ApiKey WHERE id = :id", id=created["id"]))   # only the hash

    h = key_headers(key)
    assert client.get("/knowledge/search", headers=h, params={"q": "PostgreSQL"}).json()["knowledge_items"][0]["id"] == item["id"]
    assert client.get(f"/knowledge/items/{item['id']}", headers=h).status_code == 200
    assert client.post("/knowledge/impact", headers=h, json={"proposal": "Move payments to MongoDB"}).status_code == 200
    # nothing that changes anything
    for method, path, body in (("POST", "/knowledge/artifacts", {"title": "x", "content": "We decided x."}),
                               ("PATCH", f"/knowledge/review/{item['id']}", {"status": "rejected"}),
                               ("DELETE", f"/knowledge/items/{item['id']}", None),
                               ("POST", "/knowledge/api-keys", {"name": "another"}),
                               ("POST", "/organizations", {"name": "Mine"}),
                               ("POST", "/auth/logout", None)):
        r = client.request(method, path, headers=h, json=body)
        assert r.status_code == 403, (method, path, r.status_code)
    listed = client.get("/knowledge/api-keys", headers=alice).json()
    assert listed[0]["last_used_at"] and "key" not in listed[0]


def test_a_key_stays_in_its_space_and_ends_with_its_creators_membership(client, alice, bob):
    org = create_org(client, alice, bob)
    group = create_group(client, bob, org)
    key = new_key(client, {**bob, "X-Space": group["id"]})["key"]
    ingest_text(client, {**bob, "X-Space": group["id"]}, title="Team", content="We decided to pair on reviews.")
    h = key_headers(key)
    titles = {a["title"] for a in client.get("/knowledge", headers=h).json()["artifacts"]}
    assert titles == {"Team"}                                                     # its space, without X-Space
    assert client.get("/knowledge", headers={**h, "X-Space": org}).status_code == 403
    # bob leaves the organization: his key stops working there
    client.post(f"/organizations/{org}/leave", headers=bob)
    assert client.get("/knowledge", headers=h).status_code == 403


def test_revoked_and_made_up_keys_are_refused(client, alice, bob):
    created = new_key(client, alice)
    assert client.delete(f"/knowledge/api-keys/{created['id']}", headers=bob).status_code == 404
    assert client.delete(f"/knowledge/api-keys/{created['id']}", headers=alice).status_code == 204
    assert client.get("/knowledge", headers=key_headers(created["key"])).status_code == 401
    assert client.get("/knowledge", headers=key_headers("khk_made-up")).status_code == 401


def test_an_admins_key_is_not_an_admin(client, make_user):
    admin = make_user(role="admin")
    h = key_headers(new_key(client, admin)["key"])
    assert client.get("/auth/me", headers=h).json()["role"] == "member"
    assert client.get("/admin/users", headers=h).status_code == 403
