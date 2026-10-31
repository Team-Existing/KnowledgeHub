"""Verification (duplicates and contradictions), rejected alternatives, and the one-word lifecycle."""
from __future__ import annotations

import json
from datetime import date

from app.services.health import lifecycle
from app.services.item_schema import clean_alternatives
from app.services.knowledge_extraction import KnowledgeExtraction
from tests.helpers import ingest_text

OLD = "We decided to deploy the billing service every Friday afternoon so releases batch up for the weekend."
NEW = "We decided to stop billing service deploys on Friday afternoon because weekend incidents doubled."


def decision(result):
    return next(i for i in result["items"] if i["type"] == "decision")


def accept(client, headers, item_id):
    r = client.patch(f"/knowledge/review/{item_id}", headers=headers, json={"status": "accepted"})
    assert r.status_code == 200, r.text


def signals(client, headers, item_id):
    return client.get(f"/knowledge/items/{item_id}", headers=headers).json()["health"]["signals"]


# ── lifecycle ───────────────────────────────────────────────────────────────

def test_one_lifecycle_from_status_and_health():
    today = date(2026, 6, 1)
    assert lifecycle({"status": "active"}, today) == "active"
    assert lifecycle({"status": "superseded"}, today) == "superseded"
    assert lifecycle({"status": "reversed"}, today) == "superseded"
    for status in ("invalidated", "deprecated", "rejected", "closed"):
        assert lifecycle({"status": status}, today) == "invalidated"
    assert lifecycle({"review_status": "rejected"}, today) == "invalidated"
    assert lifecycle({"status": "active", "health_signals": [{"kind": "flagged"}]}, today) == "potentially_stale"
    assert lifecycle({"status": "active", "review_due": "2026-06-05"}, today) == "needs_review"
    assert lifecycle({"type": "lesson"}, today) == "active"


def test_a_decision_can_be_declared_invalidated(client, alice):
    item = decision(ingest_text(client, alice, title="Pricing", content="We decided to buy reserved instances for the 2025 AWS pricing."))
    r = client.put(f"/knowledge/items/{item['id']}/status", headers=alice, json={"status": "invalidated", "note": "pricing changed"})
    assert r.status_code == 200
    body = client.get(f"/knowledge/items/{item['id']}", headers=alice).json()
    assert body["status"] == "invalidated" and body["lifecycle"] == "invalidated"


# ── alternatives ────────────────────────────────────────────────────────────

def test_alternatives_from_the_decision_and_the_line_after():
    k = KnowledgeExtraction()
    found = {d["what"][:20]: d.get("alternatives") for d in k.extract_decisions(
        "We decided to use PostgreSQL for payments instead of MongoDB because payments need transactions.\n"
        "Alternatives considered: DynamoDB, CockroachDB.\n"
        "We agreed to use NATS rather than Kafka.\n"
        "We decided to keep the release train over summer.")}
    assert found == {"We decided to use Po": ["MongoDB", "DynamoDB", "CockroachDB"],
                     "We agreed to use NAT": ["Kafka"], "We decided to keep t": None}
    assert clean_alternatives("Cassandra or Redis") == ["Cassandra", "Redis"]
    assert clean_alternatives(["none", "Kafka", "Kafka"]) == ["Kafka"] and clean_alternatives(None) == []


def test_alternatives_reach_impact_analysis(client, alice):
    ingest_text(client, alice, title="ADR-7", content=(
        "We decided to use PostgreSQL for the payment service instead of MongoDB because payments need transactions."))
    client.post("/knowledge/systems", headers=alice, json={"name": "Payments", "aliases": ["payment service"]})
    report = client.post("/knowledge/impact", headers=alice, json={"proposal": "Move the payment service to MongoDB"}).json()
    assert report["decisions"][0]["alternatives"] == ["MongoDB"]


def test_the_model_is_asked_for_alternatives(client, alice, llm):
    llm.handlers["transcript"] = lambda m: json.dumps({"summary": "", "risks": [], "action_items": [], "decisions": [
        {"what": "Use NATS for events", "why": "cost", "who": "Lena", "alternatives": ["Kafka", "none"]}]})
    r = client.post("/knowledge/artifacts/transcript", headers=alice, json={"title": "Events", "content": "Lena: NATS, not Kafka."})
    assert decision(r.json())["details"]["alternatives"] == ["Kafka"]
    assert "alternatives" in llm.last("transcript")[0]["content"]


# ── verification ────────────────────────────────────────────────────────────

def test_accepting_a_contradicting_decision_flags_the_older_one(client, alice, llm):
    old = decision(ingest_text(client, alice, title="Release policy 2025", content=OLD))
    new = decision(ingest_text(client, alice, title="Release policy 2026", content=NEW))
    accept(client, alice, old["id"])
    llm.handlers["verify"] = lambda m: json.dumps({"verdicts": [
        {"n": 1, "verdict": "contradicts", "reason": "One deploys on Friday afternoons, the other stops it"}]})
    accept(client, alice, new["id"])                      # runs the check (a background task)

    flagged = signals(client, alice, old["id"])
    assert [(s["kind"], s["item_id"]) for s in flagged] == [("contradicted", new["id"])]
    assert "Friday" in flagged[0]["reason"]
    assert client.get(f"/knowledge/items/{old['id']}", headers=alice).json()["lifecycle"] == "potentially_stale"
    assert signals(client, alice, new["id"]) == []         # the newer one isn't in question
    prompt = llm.last("verify")[-1]["content"]
    assert prompt.startswith("New entry: " + NEW) and OLD in prompt
    assert old["id"] in {i["id"] for i in client.get("/knowledge/health", headers=alice).json()["items"]}


def test_consistent_and_linked_items_are_left_alone(client, alice, llm):
    old = decision(ingest_text(client, alice, title="A", content=OLD))
    new = decision(ingest_text(client, alice, title="B", content=NEW))
    llm.handlers["verify"] = lambda m: json.dumps({"verdicts": [{"n": 1, "verdict": "consistent", "reason": "same"}]})
    r = client.post(f"/knowledge/items/{new['id']}/health/check", headers=alice).json()
    assert r["checked"] == 1 and r["contradictions"] == [] and r["llm_used"] is True
    # once a person said how they relate, they aren't compared again
    client.post(f"/knowledge/items/{new['id']}/lineage", headers=alice, json={"target_id": old["id"], "kind": "supersedes"})
    llm.reset()
    assert client.post(f"/knowledge/items/{new['id']}/health/check", headers=alice).json()["checked"] == 0
    assert "verify" not in llm.kinds()


def test_near_identical_items_are_possible_duplicates_without_a_model(client, alice, llm):
    first = decision(ingest_text(client, alice, title="Notes", content=OLD))
    second = decision(ingest_text(client, alice, title="Copy of notes", content=OLD + " "))
    r = client.post(f"/knowledge/items/{second['id']}/health/check", headers=alice).json()
    assert [d["id"] for d in r["duplicates"]] in ([first["id"]], [second["id"]])
    assert "verify" not in llm.kinds()                     # nothing left to ask a model about
    flagged = signals(client, alice, second["id"]) + signals(client, alice, first["id"])
    assert [s["kind"] for s in flagged] == ["possible_duplicate"]


def test_without_a_model_contradictions_are_not_guessed(client, alice, llm):
    decision(ingest_text(client, alice, title="A", content=OLD))
    new = decision(ingest_text(client, alice, title="B", content=NEW))
    r = client.post(f"/knowledge/items/{new['id']}/health/check", headers=alice).json()
    assert r["llm_used"] is False and r["contradictions"] == []


def test_the_check_can_be_turned_off_and_other_spaces_are_out_of_reach(client, alice, bob, llm, monkeypatch):
    decision(ingest_text(client, alice, title="A", content=OLD))
    new = decision(ingest_text(client, alice, title="B", content=NEW))
    monkeypatch.setenv("VERIFY_ON_ACCEPT", "off")
    accept(client, alice, new["id"])
    assert "verify" not in llm.kinds()
    assert client.post(f"/knowledge/items/{new['id']}/health/check", headers=bob).status_code == 404
