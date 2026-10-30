"""Systems, their dependencies, which items affect them, and decision impact analysis."""
from __future__ import annotations

import json
from typing import Dict

import pytest

from app.services.impact import dependents, mentions
from tests.helpers import create_group, create_org, ingest_text

MEETING = json.dumps({
    "summary": "Payments stay on PostgreSQL.",
    "decisions": [{"what": "Use PostgreSQL for the payment service", "who": "Ana",
                   "why": "payments need transactional consistency and relational reporting"}],
    "action_items": [{"task": "Tune PostgreSQL connection pooling for payments", "owner": "Omar", "due": "2030-01-01"}],
    "risks": [],
})


def system(client, headers, name, **fields) -> Dict:
    r = client.post("/knowledge/systems", headers=headers, json={"name": name, **fields})
    assert r.status_code == 201, r.text
    return r.json()


def depends(client, headers, a, b):
    r = client.post(f"/knowledge/systems/{a['id']}/dependencies", headers=headers, json={"depends_on": b["id"]})
    assert r.status_code == 201, r.text


@pytest.fixture
def world(client, alice, llm) -> Dict:
    """Payments ← Reporting ← Reconciliation (each depends on the previous), Billing alone, and the record."""
    payments = system(client, alice, "Payments", aliases=["payment service"], owner="Payments team")
    reporting = system(client, alice, "Reporting")
    reconciliation = system(client, alice, "Reconciliation")
    billing = system(client, alice, "Billing")
    depends(client, alice, reporting, payments)
    depends(client, alice, reconciliation, reporting)

    llm.handlers["transcript"] = lambda m: MEETING
    r = client.post("/knowledge/artifacts/transcript", headers=alice, json={
        "title": "Payments architecture review", "content": "Ana: payments stay on PostgreSQL.", "source_type": "transcript"})
    items = r.json()["items"]
    decision = next(i for i in items if i["type"] == "decision")
    commitment = next(i for i in items if i["type"] == "action-item")
    client.post(f"/knowledge/items/{commitment['id']}/lineage", headers=alice,
                json={"target_id": decision["id"], "kind": "implements"})
    notes = ingest_text(client, alice, title="Incident retro", content=(
        "Risk: moving the payment service to another database could lose in-flight transactions.\n"
        "Lesson learned: payment service migrations need a full dry run on production data.\n"
        "We decided to deploy the marketing site on Fridays."))["items"]
    by_type = {i["type"]: i for i in notes}
    return {"payments": payments, "reporting": reporting, "reconciliation": reconciliation, "billing": billing,
            "decision": decision, "commitment": commitment, "risk": by_type["risk"], "lesson": by_type["lesson"],
            "marketing": by_type["decision"]}


def analyze(client, headers, **body) -> Dict:
    r = client.post("/knowledge/impact", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── pure ────────────────────────────────────────────────────────────────────

def test_mentions_match_whole_names_and_aliases():
    payments = {"name": "Payments", "aliases": ["payment service", "pg"]}
    assert mentions("Move the Payment Service to MongoDB", payments)
    assert mentions("payments: retries", payments)
    assert not mentions("payments-v2 rollout", payments) and not mentions("Paymentsv2", payments)
    assert not mentions("upgrade pg", payments)            # names under 3 characters are ignored


def test_dependents_follow_the_chain_and_survive_cycles():
    deps = [{"from": "b", "to": "a"}, {"from": "c", "to": "b"}, {"from": "a", "to": "c"}, {"from": "d", "to": "x"}]
    assert dependents(["a"], deps) == {"b": ["a", "b"], "c": ["a", "b", "c"]}
    assert dependents(["a"], deps, max_depth=1) == {"b": ["a", "b"]}


# ── systems ─────────────────────────────────────────────────────────────────

def test_systems_crud_dependencies_and_counts(client, alice, bob, world):
    body = client.get("/knowledge/systems", headers=alice).json()
    by_name = {s["name"]: s for s in body["systems"]}
    assert set(by_name) == {"Payments", "Reporting", "Reconciliation", "Billing"} and "database" in body["kinds"]
    assert by_name["Reporting"]["depends_on"] == [world["payments"]["id"]]
    assert by_name["Payments"]["dependents"] == [world["reporting"]["id"]]
    assert by_name["Payments"]["aliases"] == ["payment service"] and by_name["Payments"]["owner"] == "Payments team"

    assert client.post("/knowledge/systems", headers=alice, json={"name": "payments"}).status_code == 409
    assert client.post("/knowledge/systems", headers=alice, json={"name": "X", "kind": "spaceship"}).status_code == 422
    r = client.patch(f"/knowledge/systems/{world['billing']['id']}", headers=alice, json={"aliases": [" invoicing ", ""]})
    assert r.json()["aliases"] == ["invoicing"]
    assert client.patch(f"/knowledge/systems/{world['billing']['id']}", headers=alice,
                        json={"name": "Reporting"}).status_code == 409
    assert client.post(f"/knowledge/systems/{world['billing']['id']}/dependencies", headers=alice,
                       json={"depends_on": world["billing"]["id"]}).status_code == 400

    # other spaces see none of it
    assert client.get("/knowledge/systems", headers=bob).json()["systems"] == []
    assert client.patch(f"/knowledge/systems/{world['billing']['id']}", headers=bob, json={"owner": "me"}).status_code == 404

    # deleting takes its edges, not the items
    assert client.delete(f"/knowledge/systems/{world['reporting']['id']}", headers=alice).status_code == 204
    by_name = {s["name"]: s for s in client.get("/knowledge/systems", headers=alice).json()["systems"]}
    assert by_name["Payments"]["dependents"] == [] and by_name["Reconciliation"]["depends_on"] == []
    client.delete(f"/knowledge/systems/{world['reconciliation']['id']}/dependencies/{world['payments']['id']}", headers=alice)


def test_items_link_to_the_systems_they_affect_and_named_ones_are_suggested(client, alice, world):
    risk = world["risk"]["id"]
    found = client.get(f"/knowledge/items/{risk}/systems", headers=alice).json()
    assert found == {"linked": [], "suggested": [{"id": world["payments"]["id"], "name": "Payments", "kind": "service"}]}
    assert client.post(f"/knowledge/items/{risk}/systems", headers=alice,
                       json={"system_id": world["payments"]["id"]}).status_code == 201
    found = client.get(f"/knowledge/items/{risk}/systems", headers=alice).json()
    assert [s["name"] for s in found["linked"]] == ["Payments"] and found["suggested"] == []
    counts = {s["name"]: s["item_count"] for s in client.get("/knowledge/systems", headers=alice).json()["systems"]}
    assert counts["Payments"] == 1
    assert client.delete(f"/knowledge/items/{risk}/systems/{world['payments']['id']}", headers=alice).status_code == 204
    assert client.get(f"/knowledge/items/{risk}/systems", headers=alice).json()["linked"] == []
    assert client.post(f"/knowledge/items/{risk}/systems", headers=alice, json={"system_id": "sys_nope"}).status_code == 404


# ── impact analysis ─────────────────────────────────────────────────────────

def test_a_proposed_change_shows_what_it_touches(client, alice, world, llm):
    llm.handlers["impact"] = lambda m: json.dumps({"assessments": [
        {"n": 1, "relation": "conflicts", "reason": "MongoDB drops the transactional consistency payments need"}]})
    report = analyze(client, alice, proposal="Move the payment service to MongoDB")

    systems = {s["name"]: s for s in report["systems"]}
    assert systems["Payments"]["depth"] == 0 and systems["Payments"]["reason"] == "named in the proposal"
    assert systems["Reporting"]["depth"] == 1 and systems["Reporting"]["reason"] == "depends on Payments"
    assert systems["Reconciliation"]["depth"] == 2 and systems["Reconciliation"]["via"] == ["Payments", "Reporting"]
    assert "Billing" not in systems

    first = report["decisions"][0]
    assert first["id"] == world["decision"]["id"] and "mentions Payments" in first["reasons"]
    assert first["why"].startswith("payments need transactional consistency") and first["owner"] == "Ana"
    assert first["relation"] == "conflicts" and "consistency" in first["relation_reason"]
    assert first["open_commitments"] == 1
    assert world["marketing"]["id"] not in {d["id"] for d in report["decisions"]}
    assert [r["id"] for r in report["risks"]] == [world["risk"]["id"]]
    assert [l["id"] for l in report["lessons"]] == [world["lesson"]["id"]]
    assert [c["id"] for c in report["commitments"]] == [world["commitment"]["id"]]
    assert report["llm_used"] is True and report["counts"]["conflicts"] == 1

    prompt = llm.last("impact")[-1]["content"]
    assert prompt.startswith("Proposed change: Move the payment service to MongoDB")
    assert "why: payments need transactional consistency" in prompt and "marketing" not in prompt


def test_without_a_model_the_report_still_stands(client, alice, world, llm):
    report = analyze(client, alice, proposal="Move the payment service to MongoDB")
    assert report["llm_used"] is False and report["decisions"][0]["relation"] is None
    assert report["counts"]["decisions"] >= 1 and report["counts"]["systems"] == 3


def test_turning_the_model_off(client, alice, world, llm, monkeypatch):
    monkeypatch.setenv("IMPACT_LLM", "off")
    analyze(client, alice, proposal="Move the payment service to MongoDB")
    assert "impact" not in llm.kinds()


def test_reconsidering_a_decision_starts_from_its_systems(client, alice, world):
    decision = world["decision"]["id"]
    client.post(f"/knowledge/items/{decision}/systems", headers=alice, json={"system_id": world["billing"]["id"]})
    report = analyze(client, alice, decision_id=decision)
    assert report["subject"]["id"] == decision and report["proposal"].startswith("Change or reverse the decision")
    assert {s["name"]: s["reason"] for s in report["systems"]}["Billing"] == "the decision affects it"
    assert decision not in {d["id"] for d in report["decisions"]}            # not related to itself
    # its own commitment in flight is what reconsidering it would disrupt
    assert [c["id"] for c in report["commitments"]] == [world["commitment"]["id"]]


def test_history_points_to_what_replaced_it(client, alice, world, llm):
    newer = ingest_text(client, alice, title="2026 review",
                        content="We decided to keep the payment service on PostgreSQL 17 with logical replicas.")["items"]
    newer_id = next(i["id"] for i in newer if i["type"] == "decision")
    client.post(f"/knowledge/items/{newer_id}/lineage", headers=alice,
                json={"target_id": world["decision"]["id"], "kind": "supersedes"})
    llm.handlers["impact"] = lambda m: json.dumps({"assessments": []})
    report = analyze(client, alice, proposal="Move the payment service to MongoDB")
    old = next(d for d in report["decisions"] if d["id"] == world["decision"]["id"])
    assert old["status"] == "superseded" and old["replaced_by"]["id"] == newer_id
    assert report["decisions"][-1]["id"] == world["decision"]["id"]           # history after current decisions
    assert "PostgreSQL 17" in llm.last("impact")[-1]["content"] and "[2]" not in llm.last("impact")[-1]["content"]


def test_chosen_systems_and_requests_that_say_nothing(client, alice, bob, world):
    report = analyze(client, alice, system_ids=[world["reporting"]["id"]])
    assert {s["name"]: s["depth"] for s in report["systems"]} == {"Reporting": 0, "Reconciliation": 1}
    assert client.post("/knowledge/impact", headers=alice, json={}).status_code == 422
    assert client.post("/knowledge/impact", headers=alice, json={"decision_id": world["risk"]["id"]}).status_code == 404
    assert client.post("/knowledge/impact", headers=bob, json={"decision_id": world["decision"]["id"]}).status_code == 404
    # another space's systems are ignored
    assert analyze(client, bob, proposal="Move the payment service to MongoDB",
                   system_ids=[world["payments"]["id"]])["systems"] == []


def test_deleting_a_space_deletes_its_systems(client, alice, db_query):
    group = create_group(client, alice, create_org(client, alice))
    headers = {**alice, "X-Space": group["id"]}
    a, b = system(client, headers, "Gateway"), system(client, headers, "Auth")
    depends(client, headers, a, b)
    assert client.delete(f"/groups/{group['id']}", headers=alice).status_code == 204
    assert db_query("SELECT count(*) AS n FROM System WHERE user_id = :g", g=group["id"])[0]["n"] == 0
    assert db_query("SELECT count(*) AS n FROM DEPENDS_ON WHERE user_id = :g", g=group["id"])[0]["n"] == 0
