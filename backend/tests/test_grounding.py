"""Unsupported claims: model-extracted items checked against their source (services/grounding.py)."""
from __future__ import annotations

import json

import pytest

from app.services.grounding import best_passages

SOURCE = ("Ana: billing moves to PostgreSQL because we need transaction consistency.\n"
          "Omar: I'll migrate the billing tables next week.\n"
          "Ana: and we keep the nightly export for finance.")

# the model gets one right, half of one, and invents one
EXTRACTED = json.dumps({
    "summary": "Billing moves to PostgreSQL.",
    "decisions": [{"what": "Move billing to PostgreSQL", "why": "transaction consistency", "who": "Ana"},
                  {"what": "Stop the nightly export for finance", "why": "", "who": "Ana"},
                  {"what": "Adopt Kubernetes for every service", "why": "scaling", "who": "Omar"}],
    "action_items": [], "risks": [],
})


def judge(messages):
    """The model's verdicts: the claims arrive numbered, the one the text covers isn't among them."""
    text = messages[-1]["content"]
    verdicts = []
    for n, line in enumerate((l for l in text.splitlines() if l.startswith("[")), 1):
        if "Kubernetes" in line:
            verdicts.append({"n": n, "verdict": "unsupported", "reason": "Nobody mentions Kubernetes."})
        else:
            verdicts.append({"n": n, "verdict": "partly", "reason": "The export is kept, not stopped."})
    return json.dumps({"verdicts": verdicts})


@pytest.fixture
def checking(monkeypatch, llm):
    monkeypatch.setenv("CHECK_SUPPORT", "on")
    llm.handlers["transcript"] = lambda m: EXTRACTED
    llm.handlers["ground"] = judge
    return llm


def ingest(client, headers):
    r = client.post("/knowledge/artifacts/transcript", headers=headers, json={
        "title": "Billing call", "content": SOURCE, "source_type": "transcript"})
    assert r.status_code == 200, r.text
    return {i["title"]: i["id"] for i in r.json()["items"]}


def item(client, headers, item_id):
    return client.get(f"/knowledge/items/{item_id}", headers=headers).json()


def test_best_passage_covers_the_claim():
    [(score, passage), *_] = best_passages("Move billing to PostgreSQL for transaction consistency", SOURCE)
    assert score >= 0.6 and "PostgreSQL" in passage
    assert best_passages("Adopt Kubernetes", SOURCE)[0][0] == 0


def test_extracted_items_are_checked_against_their_source(client, alice, checking):
    ids = ingest(client, alice)
    supported = item(client, alice, ids["Move billing to PostgreSQL"])["support"]
    assert supported["status"] == "supported" and supported["method"] == "text"
    assert "transaction consistency" in supported["passage"]

    partly = item(client, alice, ids["Stop the nightly export for finance"])["support"]
    assert partly["status"] == "partly" and partly["method"] == "model"
    assert "nightly export" in partly["passage"] and "kept" in partly["reason"]

    invented = item(client, alice, ids["Adopt Kubernetes for every service"])
    assert invented["support"]["status"] == "unsupported"
    assert [s["kind"] for s in invented["health"]["signals"]] == ["unsupported"]
    # one model call for the two claims the text didn't cover
    assert checking.kinds().count("ground") == 1
    assert "Move billing" not in checking.last("ground")[-1]["content"]


def test_rule_based_items_are_not_checked(client, alice, checking):
    r = client.post("/knowledge/artifacts", headers=alice, json={
        "title": "Notes", "content": "We decided to adopt PostgreSQL for billing.", "author": "ana", "tags": []})
    decision = next(i for i in r.json()["items"] if i["type"] == "decision")
    assert item(client, alice, decision["id"])["support"] is None
    assert "ground" not in checking.kinds()


def test_without_a_model_uncovered_claims_stay_unverified(client, alice, checking):
    del checking.handlers["ground"]
    ids = ingest(client, alice)
    assert item(client, alice, ids["Move billing to PostgreSQL"])["support"]["status"] == "supported"
    invented = item(client, alice, ids["Adopt Kubernetes for every service"])
    assert invented["support"]["status"] == "unverified" and not invented["health"]["signals"]


def test_check_on_demand_and_a_supported_verdict_clears_the_signal(client, alice, checking):
    ids = ingest(client, alice)
    kubernetes = ids["Adopt Kubernetes for every service"]
    checking.handlers["ground"] = lambda m: json.dumps({"verdicts": [{"n": 1, "verdict": "supported", "reason": "ok"}]})
    r = client.post(f"/knowledge/items/{kubernetes}/support/check", headers=alice)
    assert r.status_code == 200, r.text
    assert r.json()["support"]["status"] == "supported"
    assert r.json()["item"]["health"]["signals"] == []


def test_off_switch(client, alice, checking, monkeypatch):
    monkeypatch.setenv("CHECK_SUPPORT", "off")
    ids = ingest(client, alice)
    assert item(client, alice, ids["Move billing to PostgreSQL"])["support"] is None
    assert "ground" not in checking.kinds()
