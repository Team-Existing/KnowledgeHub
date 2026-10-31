"""The assistant: planning by rules, running the agents, and checking the answer (services/orchestrator.py)."""
from __future__ import annotations

from app.services.orchestrator import plan, verify_answer
from tests.helpers import ingest_text

PAYMENTS = {"id": "s1", "name": "Payments", "aliases": ["payment service"]}


def agents(question, systems=()):
    return [(s["agent"], s["args"].get("view")) for s in plan(question, list(systems))]


def test_a_change_goes_to_impact():
    assert agents("Should we move off PostgreSQL for payments?")[0] == ("impact", None)


def test_understanding_a_named_system_goes_to_its_map():
    steps = plan("What should I understand before modifying the payment service?", [PAYMENTS])
    learning = next(s for s in steps if s["agent"] == "learning")
    assert learning["args"] == {"view": "map", "system_ids": ["s1"]}
    assert "Payments" in learning["why"]


def test_learning_views_and_watchtower():
    assert ("learning", "abandoned") in agents("Which approaches have we tried and abandoned?")
    assert ("learning", "mistakes") in agents("Show our most common production mistakes")
    assert ("learning", "controversial") in agents("Which architectural decisions are controversial?")
    assert agents("List overdue commitments") == [("watchtower", None)]


def test_an_open_question_gets_an_answer_and_its_check():
    assert agents("Tell me about billing") == [("research", None), ("verification", None)]
    assert agents("Why did we pick NATS?")[-2:] == [("research", None), ("verification", None)]


def test_verification_marks_sentences_no_source_backs():
    research = {"context_nodes": [{"id": "d1", "title": "Use PostgreSQL for payments", "type": "decision",
                                   "details": {"why": "payments need transactions"}}]}
    found = verify_answer("Payments use PostgreSQL because payments need transactions [1]. "
                          "The team also runs a nightly Kubernetes rebuild on Sundays.", research)
    first, second = found["sentences"]
    assert first["backed"] and first["source"]["id"] == "d1"
    assert not second["backed"]
    assert (found["backed"], found["unbacked"]) == (1, 1)


def test_the_assistant_runs_its_plan(client, alice, llm):
    ingest_text(client, alice, title="Billing notes",
                content="We decided to use PostgreSQL for billing because billing needs transactions.")
    r = client.post("/knowledge/assistant", headers=alice,
                    json={"question": "Why do we use PostgreSQL for billing? Any overdue commitments?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["agent"] for s in body["plan"]] == ["watchtower", "research", "verification"]
    steps = {s["agent"]: s for s in body["steps"]}
    assert all(s["ok"] for s in body["steps"]), body["steps"]
    assert "overdue_commitments" in steps["watchtower"]["result"]
    assert steps["research"]["result"]["answer"]
    assert "sentences" in steps["verification"]["result"] or "skipped" in steps["verification"]["result"]


def test_a_failing_agent_does_not_stop_the_others(client, alice, monkeypatch):
    from app.services import orchestrator

    async def broken(*a, **k):
        raise RuntimeError("impact is down")
    monkeypatch.setattr(orchestrator, "_impact", broken)
    r = client.post("/knowledge/assistant", headers=alice, json={"question": "Should we migrate billing? Anything overdue?"})
    steps = {s["agent"]: s for s in r.json()["steps"]}
    assert steps["impact"] == {**steps["impact"], "ok": False, "error": "impact is down"}
    assert steps["watchtower"]["ok"]
