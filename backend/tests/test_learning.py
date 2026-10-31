"""Institutional learning: the knowledge map, what was tried and abandoned, recurring mistakes, controversy."""
from __future__ import annotations

from typing import Dict

import pytest

from tests.helpers import ingest_text


def first(result, kind, word=""):
    return next(i for i in result["items"] if i["type"] == kind and word.lower() in i["title"].lower())


def link(client, headers, a, b, kind):
    r = client.post(f"/knowledge/items/{a['id']}/lineage", headers=headers, json={"target_id": b["id"], "kind": kind})
    assert r.status_code == 201, r.text


@pytest.fixture
def history(client, alice) -> Dict:
    h = alice
    payments = client.post("/knowledge/systems", headers=h, json={"name": "Payments", "aliases": ["payment service"], "owner": "Backend team"}).json()
    events = client.post("/knowledge/systems", headers=h, json={"name": "Events", "aliases": ["Kafka", "NATS"]}).json()
    client.post(f"/knowledge/systems/{payments['id']}/dependencies", headers=h, json={"depends_on": events["id"]})
    kafka = first(ingest_text(client, h, title="2024", content="We decided to publish payment service events to Kafka."), "decision")
    nats = first(ingest_text(client, h, title="2025", content=(
        "We decided to replace Kafka with NATS because the Kafka cluster costs too much.")), "decision")
    link(client, h, nats, kafka, "supersedes")
    pg = first(ingest_text(client, h, title="ADR-1", content=(
        "We decided to use PostgreSQL for the payment service instead of MongoDB because payments need transactions.")), "decision")
    cache = first(ingest_text(client, h, title="Caching", content="We decided to cache payment service sessions in Redis."), "decision")
    nocache = first(ingest_text(client, h, title="Caching 2", content=(
        "We decided to stop caching payment service sessions in Redis because failovers lost sessions.")), "decision")
    link(client, h, nocache, cache, "reverses")
    for n, text in enumerate(("We decided that refunds over 1000 need a second approval.",
                              "We decided that refunds over 5000 need a manager approval.")):
        amendment = first(ingest_text(client, h, title=f"Refund rule {n}", content=text), "decision")
        if n == 0:
            refunds = amendment
        else:
            link(client, h, amendment, refunds, "amends")
    rule3 = first(ingest_text(client, h, title="Refund rule 3", content="We decided that refunds need a reason code."), "decision")
    link(client, h, rule3, refunds, "amends")
    client.put(f"/knowledge/items/{refunds['id']}/outcome", headers=h, json={"verdict": "not_achieved", "note": "fraud went up"})
    lesson1 = first(ingest_text(client, h, title="Retro 1", content=(
        "Lesson learned: payment service migrations need a dry run on a copy of production data.")), "lesson")
    lesson2 = first(ingest_text(client, h, title="Retro 2", content=(
        "Lesson learned: payment service migrations need a dry run on a full copy of production data first.")), "lesson")
    other = first(ingest_text(client, h, title="Retro 3", content="Lesson learned: verbal pager handovers lose context overnight."), "lesson")
    risk = first(ingest_text(client, h, title="Risks", content="Risk: a payment service migration could lose in-flight transactions."), "risk")
    link(client, h, lesson1, risk, "realizes")
    return {"payments": payments, "events": events, "kafka": kafka, "nats": nats, "pg": pg, "cache": cache,
            "nocache": nocache, "refunds": refunds, "lesson1": lesson1, "lesson2": lesson2, "other": other, "risk": risk}


def get(client, headers, path, **params):
    r = client.get(f"/knowledge/learning/{path}", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_knowledge_map_of_a_system(client, alice, history):
    body = get(client, alice, "map", system=history["payments"]["id"])
    assert [s["name"] for s in body["all_systems"]] == ["Events", "Payments"]
    (m,) = body["systems"]
    assert m["system"]["name"] == "Payments" and m["depends_on"] == ["Events"] and m["dependents"] == []
    current = {d["id"] for d in m["current"]}
    assert history["pg"]["id"] in current and history["nocache"]["id"] in current
    assert history["cache"]["id"] not in current                                  # reversed: history, not current
    pg = next(d for d in m["current"] if d["id"] == history["pg"]["id"])
    assert pg["alternatives"] == ["MongoDB"] and pg["why"].startswith("because payments need transactions")
    cache = next(d for d in m["history"] if d["id"] == history["cache"]["id"])
    assert cache["replaced_by"] == {"id": history["nocache"]["id"], "title": history["nocache"]["title"], "kind": "reverses"}
    assert {l["id"] for l in m["lessons"]} == {history["lesson1"]["id"], history["lesson2"]["id"]}
    assert [r["id"] for r in m["risks"]] == [history["risk"]["id"]]
    # every system when none is chosen
    assert len(get(client, alice, "map")["systems"]) == 2


def test_tried_and_abandoned(client, alice, history):
    body = get(client, alice, "abandoned")
    by_id = {d["id"]: d for d in body["decisions"]}
    assert by_id[history["kafka"]["id"]]["how"] == "superseded"
    assert by_id[history["kafka"]["id"]]["replaced_by"]["why"].startswith("because the Kafka cluster costs too much")
    assert by_id[history["cache"]["id"]]["how"] == "reversed"
    assert by_id[history["refunds"]["id"]]["how"] == "outcome not achieved" and by_id[history["refunds"]["id"]]["note"] == "fraud went up"
    assert history["pg"]["id"] not in by_id
    assert {"option": "MongoDB", "instead": {"id": history["pg"]["id"], "title": history["pg"]["title"]},
            "why": history["pg"]["details"]["why"]} in body["rejected_alternatives"]


def test_recurring_mistakes_group_similar_lessons_and_risks_that_happened(client, alice, history):
    groups = get(client, alice, "mistakes")["groups"]
    top = groups[0]
    assert top["count"] >= 2 and "Payments" in top["systems"]
    assert {history["lesson1"]["id"], history["lesson2"]["id"]} <= {i["id"] for i in top["items"]}
    # the unrelated lesson isn't counted as the same mistake (the test embeddings are only bags of words,
    # so it may share a group with something else, but not with the migration lessons)
    assert history["other"]["id"] not in {i["id"] for i in top["items"]}
    # a risk counts once it happened
    assert history["risk"]["id"] in {i["id"] for g in groups for i in g["items"]}


def test_controversial_decisions_with_their_reasons(client, alice, history):
    ranked = get(client, alice, "controversial")["decisions"]
    by_id = {d["id"]: d for d in ranked}
    refunds = by_id[history["refunds"]["id"]]
    assert "amended 2 times" in refunds["reasons"] and "its outcome was not achieved" in refunds["reasons"]
    assert "part of a 3-step decision history" in refunds["reasons"]
    assert ranked[0]["id"] == history["refunds"]["id"]                           # the most signs first
    assert "reversed, or reverses an earlier decision" in by_id[history["cache"]["id"]]["reasons"]
    assert history["pg"]["id"] not in by_id


def test_other_spaces_learn_nothing_from_this_one(client, bob, history):
    assert get(client, bob, "map")["systems"] == []
    assert get(client, bob, "abandoned")["decisions"] == []
    assert get(client, bob, "controversial")["decisions"] == []
