"""Playbooks: stable per-space ids, list, replace by title, delete, steps normalised."""
from __future__ import annotations

from app.services.curation_layer import CurationLayer


def test_playbook_ids_are_stable_and_per_space():
    c = CurationLayer()
    a = c.build_playbook("space_a", "Release", [{"text": "x"}])
    assert a["id"] == c.build_playbook("space_a", " release ", [{"text": "y"}])["id"]   # same title, any casing
    assert a["id"] != c.build_playbook("space_b", "Release", [{"text": "x"}])["id"]
    assert c.normalize_steps([{"title": " a "}, {"text": ""}, {"step": "b", "item_id": "how_1"}]) == \
        [{"text": "a"}, {"text": "b", "item_id": "how_1"}]


def test_create_list_replace_and_delete(client, alice):
    r = client.post("/knowledge/playbooks", headers=alice,
                    json={"title": "Release", "steps": [{"text": "Freeze main"}, {"text": "Tag"}]})
    assert r.status_code == 200
    pid = r.json()["id"]
    r = client.post("/knowledge/playbooks", headers=alice, json={"title": "release", "steps": [{"text": "Only step"}]})
    assert r.json()["id"] == pid
    listed = client.get("/knowledge/playbooks", headers=alice).json()
    assert [(p["id"], [s["text"] for s in p["steps"]]) for p in listed] == [(pid, ["Only step"])]
    assert client.delete(f"/knowledge/playbooks/{pid}", headers=alice).status_code == 204
    assert client.get("/knowledge/playbooks", headers=alice).json() == []
    assert client.delete(f"/knowledge/playbooks/{pid}", headers=alice).status_code == 404


def test_playbooks_need_steps_and_stay_private(client, alice, bob):
    assert client.post("/knowledge/playbooks", headers=alice,
                       json={"title": "Empty", "steps": [{"text": "  "}]}).status_code == 400
    pid = client.post("/knowledge/playbooks", headers=alice,
                      json={"title": "Mine", "steps": [{"text": "a"}]}).json()["id"]
    assert client.get("/knowledge/playbooks", headers=bob).json() == []
    assert client.delete(f"/knowledge/playbooks/{pid}", headers=bob).status_code == 404
    # bob can use the same title in his own space without a clash
    assert client.post("/knowledge/playbooks", headers=bob,
                       json={"title": "Mine", "steps": [{"text": "b"}]}).status_code == 200
