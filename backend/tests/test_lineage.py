"""Decision tracking over time: lineage links, derived statuses, history, suggestions, GraphRAG awareness."""
from __future__ import annotations

import pytest

from app.services.lineage import LineageError, derive_status, validate_link
from tests.helpers import ingest_text


# ── pure rules (no database) ────────────────────────────────────────────────

def test_status_derivation_precedence():
    assert derive_status("decision", None, []) == "active"
    assert derive_status("decision", "proposed", []) == "proposed"
    assert derive_status("decision", "proposed", ["supersedes"]) == "superseded"
    assert derive_status("decision", None, ["supersedes", "reverses"]) == "reversed"
    assert derive_status("decision", None, ["amends"]) == "active"
    assert derive_status("risk", None, []) == "open"
    assert derive_status("risk", "closed", ["mitigates"]) == "mitigated"
    assert derive_status("risk", None, ["mitigates", "realizes"]) == "materialized"
    assert derive_status("lesson", None, ["learned_from"]) is None


def _it(id_, type_):
    return {"id": id_, "type": type_}


def test_link_validation_rules():
    d1, d2, d3, risk, lesson = _it("d1", "decision"), _it("d2", "decision"), _it("d3", "decision"), \
        _it("r", "risk"), _it("l", "lesson")
    validate_link(d2, d1, "supersedes", [])
    validate_link(lesson, risk, "realizes", [])
    validate_link(d1, risk, "mitigates", [])
    with pytest.raises(LineageError, match="Unknown link kind"):
        validate_link(d2, d1, "replaces", [])
    with pytest.raises(LineageError, match="itself"):
        validate_link(d1, d1, "supersedes", [])
    with pytest.raises(LineageError, match="point to a risk"):
        validate_link(lesson, d1, "realizes", [])
    with pytest.raises(LineageError, match="start from a decision"):
        validate_link(lesson, d1, "supersedes", [])
    edges = [{"from": "d2", "to": "d1", "kind": "supersedes"}, {"from": "d3", "to": "d2", "kind": "supersedes"}]
    with pytest.raises(LineageError, match="already linked") as exc:
        validate_link(d1, d2, "amends", edges)
    assert exc.value.status_code == 409
    with pytest.raises(LineageError, match="circular"):
        validate_link(d1, d3, "supersedes", edges)   # d3 -> d2 -> d1 already


# ── API ─────────────────────────────────────────────────────────────────────

OLD_DECISION = "We decided to adopt MySQL for the billing database because the team knows it."
NEW_DECISION = "We decided to move the billing database to PostgreSQL because MySQL lacked JSON support."


def _decision(client, headers, title, content):
    result = ingest_text(client, headers, title=title, content=content, tags=("billing",))
    return next(i for i in result["items"] if i["type"] == "decision")


@pytest.fixture
def two_decisions(client, alice):
    old = _decision(client, alice, "2023 database choice", OLD_DECISION)
    new = _decision(client, alice, "2025 database migration", NEW_DECISION)
    return old, new


def _item(client, headers, item_id):
    r = client.get(f"/knowledge/items/{item_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_new_decisions_and_risks_start_active_and_open(client, alice):
    result = ingest_text(client, alice)
    statuses = {i["type"]: _item(client, alice, i["id"])["status"] for i in result["items"]}
    assert statuses["decision"] == "active"
    assert statuses["risk"] == "open"
    assert statuses["lesson"] is None


def test_supersede_marks_old_decision_and_logs_history(client, alice, two_decisions):
    old, new = two_decisions
    r = client.post(f"/knowledge/items/{new['id']}/lineage", headers=alice,
                    json={"target_id": old["id"], "kind": "supersedes", "note": "JSON support"})
    assert r.status_code == 201, r.text
    assert {"id": old["id"], "from": "active", "to": "superseded"} in r.json()["status_changes"]

    assert _item(client, alice, old["id"])["status"] == "superseded"
    assert _item(client, alice, new["id"])["status"] == "active"

    lin = client.get(f"/knowledge/items/{old['id']}/lineage", headers=alice).json()
    assert [l["item"]["id"] for l in lin["links"]["incoming"]] == [new["id"]]
    assert lin["links"]["incoming"][0]["kind"] == "supersedes"
    assert lin["links"]["incoming"][0]["note"] == "JSON support"
    assert [c["id"] for c in lin["chain"]] == [old["id"], new["id"]]          # oldest first
    kinds = [e["kind"] for e in lin["events"]]
    assert "linked" in kinds and "status_changed" in kinds
    changed = next(e for e in lin["events"] if e["kind"] == "status_changed")
    assert changed["detail"] == {"from": "active", "to": "superseded"}

    # graph + relationship listing carry the lineage edge
    rels = client.get("/knowledge", headers=alice).json()["relationships"]
    assert {"from": new["id"], "to": old["id"], "type": "SUPERSEDES"} in rels


def test_removing_link_or_successor_reactivates_old_decision(client, alice, two_decisions):
    old, new = two_decisions
    client.post(f"/knowledge/items/{new['id']}/lineage", headers=alice,
                json={"target_id": old["id"], "kind": "supersedes"})
    # direction is irrelevant when unlinking
    r = client.delete(f"/knowledge/items/{old['id']}/lineage/{new['id']}", headers=alice)
    assert r.status_code == 200, r.text
    assert _item(client, alice, old["id"])["status"] == "active"

    client.post(f"/knowledge/items/{new['id']}/lineage", headers=alice,
                json={"target_id": old["id"], "kind": "supersedes"})
    assert client.delete(f"/knowledge/items/{new['id']}", headers=alice).status_code == 204
    assert _item(client, alice, old["id"])["status"] == "active"


def test_incoming_direction_and_invalid_links(client, alice, two_decisions):
    old, new = two_decisions
    r = client.post(f"/knowledge/items/{old['id']}/lineage", headers=alice,
                    json={"target_id": new["id"], "kind": "reverses", "direction": "in"})
    assert r.status_code == 201 and r.json()["from"] == new["id"]
    assert _item(client, alice, old["id"])["status"] == "reversed"
    # a second link between the same pair, or a cycle, is refused
    r = client.post(f"/knowledge/items/{old['id']}/lineage", headers=alice,
                    json={"target_id": new["id"], "kind": "supersedes"})
    assert r.status_code == 409
    r = client.post(f"/knowledge/items/{old['id']}/lineage", headers=alice,
                    json={"target_id": "decision_doesnotexist", "kind": "supersedes"})
    assert r.status_code == 404


def test_declared_status_and_edges_win(client, alice, two_decisions):
    old, new = two_decisions
    r = client.put(f"/knowledge/items/{old['id']}/status", headers=alice, json={"status": "deprecated", "note": "x"})
    assert r.status_code == 200 and r.json()["status"] == "deprecated"
    assert client.put(f"/knowledge/items/{old['id']}/status", headers=alice,
                      json={"status": "materialized"}).status_code == 400
    client.post(f"/knowledge/items/{new['id']}/lineage", headers=alice,
                json={"target_id": old["id"], "kind": "supersedes"})
    assert _item(client, alice, old["id"])["status"] == "superseded"
    # clearing the declaration falls back to edges / default
    r = client.put(f"/knowledge/items/{new['id']}/status", headers=alice, json={"status": None})
    assert r.json()["status"] == "active"


def test_risk_lifecycle(client, alice):
    result = ingest_text(client, alice, title="Risks", content=(
        "Risk: the payment vendor outage could block checkout. "
        "Lesson learned: the payment vendor outage blocked checkout for two hours."))
    risk = next(i for i in result["items"] if i["type"] == "risk")
    lesson = next(i for i in result["items"] if i["type"] == "lesson")
    r = client.post(f"/knowledge/items/{lesson['id']}/lineage", headers=alice,
                    json={"target_id": risk["id"], "kind": "realizes"})
    assert r.status_code == 201, r.text
    assert _item(client, alice, risk["id"])["status"] == "materialized"
    assert client.post(f"/knowledge/items/{risk['id']}/lineage", headers=alice,
                       json={"target_id": lesson["id"], "kind": "supersedes"}).status_code == 400


def test_review_and_edit_are_recorded_in_history(client, alice, two_decisions):
    old, _ = two_decisions
    client.patch(f"/knowledge/review/{old['id']}", headers=alice, json={"status": "accepted", "note": "ok"})
    client.put(f"/knowledge/items/{old['id']}", headers=alice, json={"title": "Adopt MySQL for billing"})
    client.put(f"/knowledge/items/{old['id']}", headers=alice, json={"title": "Adopt MySQL for billing"})  # no-op
    events = client.get(f"/knowledge/items/{old['id']}/lineage", headers=alice).json()["events"]
    assert [e["kind"] for e in events] == ["reviewed", "edited"]
    assert events[0]["detail"]["to"] == "accepted"
    assert events[1]["detail"]["fields"] == ["title"]


def test_suggestions_propose_supersede_newer_over_older(client, alice, two_decisions):
    old, new = two_decisions
    suggestions = client.get(f"/knowledge/items/{old['id']}/lineage/suggestions", headers=alice).json()
    match = next(s for s in suggestions if s["item"]["id"] == new["id"])
    assert match["kind"] == "supersedes"
    assert (match["from"], match["to"]) == (new["id"], old["id"])
    # once linked it is no longer suggested
    client.post(f"/knowledge/items/{new['id']}/lineage", headers=alice,
                json={"target_id": old["id"], "kind": "supersedes"})
    suggestions = client.get(f"/knowledge/items/{old['id']}/lineage/suggestions", headers=alice).json()
    assert new["id"] not in {s["item"]["id"] for s in suggestions}


def test_register_lists_decisions_with_status(client, alice, two_decisions):
    old, new = two_decisions
    client.post(f"/knowledge/items/{new['id']}/lineage", headers=alice,
                json={"target_id": old["id"], "kind": "supersedes"})
    reg = client.get("/knowledge/register", headers=alice, params={"type": "decision"}).json()
    status = {i["id"]: i["status"] for i in reg["items"]}
    assert status[old["id"]] == "superseded" and status[new["id"]] == "active"
    assert any(e["from"] == new["id"] and e["to"] == old["id"] for e in reg["edges"])
    assert all(i["type"] == "decision" for i in reg["items"])


def test_lineage_is_private(client, alice, bob, two_decisions):
    old, new = two_decisions
    bob_item = _decision(client, bob, "Bob decision", "We decided to use Redis for caching sessions.")
    assert client.get(f"/knowledge/items/{old['id']}/lineage", headers=bob).status_code == 404
    # bob can't link his item to alice's, in either direction
    for body in ({"target_id": old["id"], "kind": "supersedes"},
                 {"target_id": old["id"], "kind": "supersedes", "direction": "in"}):
        assert client.post(f"/knowledge/items/{bob_item['id']}/lineage", headers=bob, json=body).status_code == 404
    assert _item(client, alice, old["id"])["status"] == "active"


def test_graphrag_marks_superseded_decisions_and_names_successor(client, alice, two_decisions, llm):
    old, new = two_decisions
    client.post(f"/knowledge/items/{new['id']}/lineage", headers=alice,
                json={"target_id": old["id"], "kind": "supersedes"})
    llm.handlers["generate"] = lambda messages: f"Use PostgreSQL [{new['id']}]."
    r = client.post("/knowledge/graphrag/query", headers=alice,
                    json={"question": "Which database do we use for billing?"})
    assert r.status_code == 200, r.text
    context = llm.last("generate")[-1]["content"]
    system = llm.last("generate")[0]["content"]
    assert "status=superseded" in context
    assert f"replaced_by: [{new['id']}]" in context
    assert "history, not current policy" in system
