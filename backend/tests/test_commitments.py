"""Commitments (action items: status, owner, due date, the decision they carry out) and decision outcomes."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List

import json

import pytest

from app.services.tracking import commitment_view, parse_date
from tests.helpers import ingest_text

PAST = (date.today() - timedelta(days=3)).isoformat()
FUTURE = (date.today() + timedelta(days=10)).isoformat()

# what the model extracts from the billing meeting: action items only come from LLM extraction
MEETING = json.dumps({
    "summary": "Billing moves to PostgreSQL.",
    "decisions": [{"what": "Move billing to PostgreSQL", "why": "transaction consistency", "who": "Ana"}],
    "action_items": [{"task": "Migrate billing tables to PostgreSQL", "owner": "Omar", "due": PAST},
                     {"task": "Update the billing runbook for PostgreSQL", "owner": "", "due": "end of sprint"}],
    "risks": [],
})


@pytest.fixture
def kb(client, alice, llm) -> Dict[str, Dict]:
    """A decision and the two commitments from the same meeting, plus a lesson from a later retro."""
    llm.handlers["transcript"] = lambda m: MEETING
    r = client.post("/knowledge/artifacts/transcript", headers=alice, json={
        "title": "Billing meeting", "content": "Ana: billing moves to PostgreSQL. Omar migrates the tables.",
        "source_type": "transcript"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    decision = next(i for i in items if i["type"] == "decision")
    actions = sorted((i for i in items if i["type"] == "action-item"), key=lambda i: i["title"])
    retro = ingest_text(client, alice, title="Billing retro",
                        content="Lesson learned: test billing migrations on a copy of production data.")
    lesson = next(i for i in retro["items"] if i["type"] == "lesson")
    return {"decision": decision, "migrate": actions[0], "runbook": actions[1], "lesson": lesson}


def put(client, headers, path, body):
    r = client.put(path, headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def commitments(client, headers, show="all") -> Dict:
    r = client.get("/knowledge/commitments", headers=headers, params={"show": show})
    assert r.status_code == 200, r.text
    return r.json()


def events(client, headers, item_id) -> List[str]:
    return [e["kind"] for e in client.get(f"/knowledge/items/{item_id}/lineage", headers=headers).json()["events"]]


# ── pure ────────────────────────────────────────────────────────────────────

def test_due_dates_are_parsed_only_when_they_are_dates():
    assert parse_date("2026-12-01") == "2026-12-01" and parse_date("2026-12-01T10:00") == "2026-12-01"
    assert parse_date("by Friday") is None and parse_date(None) is None
    today = date(2026, 6, 1)
    item = {"type": "action-item", "details": {"who": "Ana", "due": "2026-05-30"}}
    assert commitment_view(item, today) == {"owner": "Ana", "due_date": "2026-05-30", "due_text": None, "overdue": True}
    assert commitment_view({**item, "status": "done"}, today)["overdue"] is False
    assert commitment_view({**item, "owner": "Omar", "due_date": "2026-07-01"}, today)["owner"] == "Omar"
    words = commitment_view({"details": {"due": "end of sprint"}}, today)
    assert words["due_date"] is None and words["due_text"] == "end of sprint" and not words["overdue"]


# ── commitments ─────────────────────────────────────────────────────────────

def test_action_items_have_a_commitment_lifecycle(client, alice, kb):
    item_id = kb["migrate"]["id"]
    assert client.get(f"/knowledge/items/{item_id}", headers=alice).json()["status"] == "open"
    lineage = client.get(f"/knowledge/items/{item_id}/lineage", headers=alice).json()
    assert lineage["declarable_statuses"] == ["open", "in_progress", "done", "dropped"]
    put(client, alice, f"/knowledge/items/{item_id}/status", {"status": "in_progress"})
    assert client.get(f"/knowledge/items/{item_id}", headers=alice).json()["status"] == "in_progress"
    r = client.put(f"/knowledge/items/{item_id}/status", headers=alice, json={"status": "superseded"})
    assert r.status_code == 400


def test_owner_and_due_date_come_from_extraction_and_can_be_set(client, alice, kb):
    item_id = kb["migrate"]["id"]
    entry = next(c for c in commitments(client, alice)["items"] if c["id"] == item_id)
    assert (entry["owner"], entry["due_date"], entry["overdue"]) == ("Omar", PAST, True)
    words = next(c for c in commitments(client, alice)["items"] if c["id"] == kb["runbook"]["id"])
    assert (words["due_date"], words["due_text"], words["overdue"]) == (None, "end of sprint", False)

    updated = put(client, alice, f"/knowledge/items/{item_id}/commitment", {"owner": "Platform team", "due_date": FUTURE})
    assert updated["commitment"] == {"owner": "Platform team", "due_date": FUTURE, "due_text": None, "overdue": False}
    # only what's sent changes; null clears back to what extraction found
    assert put(client, alice, f"/knowledge/items/{item_id}/commitment", {"owner": None})["commitment"]["owner"] == "Omar"
    assert put(client, alice, f"/knowledge/items/{item_id}/commitment", {})["commitment"]["due_date"] == FUTURE
    assert "commitment_updated" in events(client, alice, item_id)

    r = client.put(f"/knowledge/items/{kb['decision']['id']}/commitment", headers=alice, json={"owner": "x"})
    assert r.status_code == 400
    assert client.put("/knowledge/items/nope/commitment", headers=alice, json={}).status_code == 404


def test_the_commitments_list_is_ordered_counted_and_filtered(client, alice, kb):
    migrate, runbook = kb["migrate"]["id"], kb["runbook"]["id"]
    put(client, alice, f"/knowledge/items/{migrate}/commitment", {"due_date": PAST})
    put(client, alice, f"/knowledge/items/{runbook}/commitment", {"due_date": FUTURE})
    body = commitments(client, alice)
    assert [c["id"] for c in body["items"]][:2] == [migrate, runbook]          # overdue first
    assert body["counts"]["overdue"] == 1 and body["counts"]["open"] == 2

    put(client, alice, f"/knowledge/items/{runbook}/status", {"status": "done"})
    assert [c["id"] for c in commitments(client, alice, "overdue")["items"]] == [migrate]
    assert [c["id"] for c in commitments(client, alice, "done")["items"]] == [runbook]
    assert runbook not in {c["id"] for c in commitments(client, alice, "open")["items"]}
    counts = commitments(client, alice)["counts"]
    assert counts["done"] == 1 and counts["open"] == 1
    r = client.get("/knowledge/commitments", headers=alice, params={"show": "everything"})
    assert r.status_code == 422


def test_commitments_carry_out_decisions(client, alice, kb):
    decision, migrate = kb["decision"]["id"], kb["migrate"]["id"]
    r = client.post(f"/knowledge/items/{migrate}/lineage", headers=alice, json={"target_id": decision, "kind": "implements"})
    assert r.status_code == 201
    entry = next(c for c in commitments(client, alice)["items"] if c["id"] == migrate)
    assert entry["decision"]["id"] == decision and entry["decision"]["title"] == kb["decision"]["title"]
    # only commitment -> decision
    r = client.post(f"/knowledge/items/{decision}/lineage", headers=alice,
                    json={"target_id": kb["runbook"]["id"], "kind": "implements"})
    assert r.status_code == 400
    kinds = client.get("/knowledge/lineage/kinds", headers=alice).json()
    assert any(k["kind"] == "implements" for k in kinds["kinds"])


def test_a_commitment_is_suggested_for_the_decision_from_the_same_meeting(client, alice, kb):
    suggestions = client.get(f"/knowledge/items/{kb['migrate']['id']}/lineage/suggestions", headers=alice).json()
    assert {(s["kind"], s["from"], s["to"]) for s in suggestions} >= {("implements", kb["migrate"]["id"], kb["decision"]["id"])}


def test_finished_commitments_are_not_questioned(client, alice, kb):
    runbook = kb["runbook"]["id"]
    put(client, alice, f"/knowledge/items/{runbook}/status", {"status": "done"})
    for item_id in (runbook, kb["migrate"]["id"]):
        client.post(f"/knowledge/items/{item_id}/health/flag", headers=alice, json={"note": "the plan changed"})
    flagged = {i["id"] for i in client.get("/knowledge/health", headers=alice).json()["items"]}
    assert kb["migrate"]["id"] in flagged and runbook not in flagged


# ── outcomes ────────────────────────────────────────────────────────────────

def test_expected_outcome_then_verdict_with_evidence(client, alice, kb):
    decision = kb["decision"]["id"]
    item = put(client, alice, f"/knowledge/items/{decision}/outcome/expectation",
               {"expected": "No lost transactions after the move", "check_by": PAST})
    assert item["outcome"]["expected"] == "No lost transactions after the move" and item["outcome"]["due"] is True

    client.post(f"/knowledge/items/{kb['migrate']['id']}/lineage", headers=alice,
                json={"target_id": decision, "kind": "implements"})
    client.post(f"/knowledge/items/{kb['runbook']['id']}/lineage", headers=alice,
                json={"target_id": decision, "kind": "implements"})
    put(client, alice, f"/knowledge/items/{kb['runbook']['id']}/status", {"status": "done"})
    client.post(f"/knowledge/items/{kb['lesson']['id']}/lineage", headers=alice,
                json={"target_id": decision, "kind": "learned_from"})

    body = client.get("/knowledge/outcomes", headers=alice).json()
    waiting = next(d for d in body["awaiting"] if d["id"] == decision)
    assert waiting["commitments"] == {"total": 2, "done": 1, "overdue": 1}     # the migration was due 3 days ago
    assert [l["id"] for l in waiting["lessons"]] == [kb["lesson"]["id"]]
    assert body["counts"]["awaiting"] >= 1 and body["counts"]["due"] >= 1

    item = put(client, alice, f"/knowledge/items/{decision}/outcome", {"verdict": "achieved", "note": "Zero incidents in Q3"})
    assert item["outcome"]["verdict"] == "achieved" and item["outcome"]["recorded_by"]
    assert item["outcome"]["due"] is False
    body = client.get("/knowledge/outcomes", headers=alice).json()
    assert decision in {d["id"] for d in body["recorded"]} and decision not in {d["id"] for d in body["awaiting"]}
    assert body["counts"]["achieved"] == 1
    assert {"outcome_expected", "outcome_recorded"} <= set(events(client, alice, decision))

    # withdrawing the verdict puts it back to waiting
    put(client, alice, f"/knowledge/items/{decision}/outcome", {"verdict": None})
    assert decision in {d["id"] for d in client.get("/knowledge/outcomes", headers=alice).json()["awaiting"]}


def test_a_missed_outcome_is_flagged_for_review(client, alice, kb):
    decision = kb["decision"]["id"]
    put(client, alice, f"/knowledge/items/{decision}/outcome", {"verdict": "not_achieved", "note": "Latency doubled"})
    item = client.get(f"/knowledge/items/{decision}", headers=alice).json()
    assert item["health"]["state"] == "potentially_stale"
    assert [s["kind"] for s in item["health"]["signals"]] == ["outcome_not_achieved"]
    assert decision in {i["id"] for i in client.get("/knowledge/health", headers=alice).json()["items"]}


def test_outcomes_are_for_decisions_only(client, alice, kb):
    path = f"/knowledge/items/{kb['migrate']['id']}"
    assert client.put(f"{path}/outcome", headers=alice, json={"verdict": "achieved"}).status_code == 400
    assert client.put(f"{path}/outcome/expectation", headers=alice, json={"expected": "x"}).status_code == 400
    r = client.put(f"/knowledge/items/{kb['decision']['id']}/outcome", headers=alice, json={"verdict": "great"})
    assert r.status_code == 422


def test_other_spaces_see_nothing(client, alice, bob, kb):
    assert commitments(client, bob)["total"] == 0
    assert client.get("/knowledge/outcomes", headers=bob).json()["awaiting"] == []
    r = client.put(f"/knowledge/items/{kb['migrate']['id']}/commitment", headers=bob, json={"owner": "me"})
    assert r.status_code == 404


def test_graphrag_sees_owners_due_dates_and_outcomes(client, alice, kb, llm):
    put(client, alice, f"/knowledge/items/{kb['migrate']['id']}/commitment", {"owner": "Ana", "due_date": PAST})
    put(client, alice, f"/knowledge/items/{kb['decision']['id']}/outcome", {"verdict": "partly", "note": "reports still slow"})
    llm.handlers["generate"] = lambda messages: "ok [1]"
    client.post("/knowledge/graphrag/query", headers=alice, json={"question": "Who migrates billing to PostgreSQL?"})
    context = llm.last("generate")[-1]["content"]
    assert "owner: Ana" in context and f"due: {PAST} (overdue)" in context
    assert "outcome: partly: reports still slow" in context
