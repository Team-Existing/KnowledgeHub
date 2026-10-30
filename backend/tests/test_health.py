"""Knowledge health: signals from changed sources and dependencies, flagging, confirming, review dates."""
from __future__ import annotations

from datetime import date, timedelta

from app.services.health import health_of
from tests.helpers import ARCH_NOTES, ingest_text


# ── pure rules (no database) ────────────────────────────────────────────────

def test_health_state_comes_from_evidence_and_review_dates_not_age():
    today = date(2026, 10, 30)
    assert health_of({"date": "2019-01-01"}, today)["state"] == "ok"    # old alone isn't stale
    assert health_of({"health_signals": [{"kind": "flagged"}]}, today)["state"] == "potentially_stale"
    assert health_of({"review_due": "2026-11-10"}, today)["state"] == "needs_review"
    assert health_of({"review_due": "2027-01-01"}, today)["state"] == "ok"
    overdue = health_of({"review_due": "2026-10-01"}, today)
    assert overdue["state"] == "needs_review" and overdue["overdue"]
    # a signal outranks a review date
    assert health_of({"health_signals": [{"kind": "flagged"}], "review_due": "2026-11-01"}, today)["state"] \
        == "potentially_stale"


# ── API ─────────────────────────────────────────────────────────────────────

def _items(client, headers):
    return {i["id"]: i for i in client.get("/knowledge", headers=headers).json()["knowledge_items"]}


def _item(client, headers, item_id):
    r = client.get(f"/knowledge/items/{item_id}/lineage", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _accept(client, headers, item_id):
    assert client.patch(f"/knowledge/review/{item_id}", headers=headers,
                        json={"status": "accepted"}).status_code == 200


def _by_type(result, type_):
    return next(i for i in result["items"] if i["type"] == type_)


def _link(client, headers, source, target, kind):
    r = client.post(f"/knowledge/items/{source}/lineage", headers=headers, json={"target_id": target, "kind": kind})
    assert r.status_code == 201, r.text


def _decision(client, headers, title, content):
    return _by_type(ingest_text(client, headers, title=title, content=content, tags=("billing",)), "decision")


def test_a_changed_source_flags_what_was_accepted_and_keeps_what_it_dropped(client, alice):
    result = ingest_text(client, alice)
    decision, lesson, risk = _by_type(result, "decision"), _by_type(result, "lesson"), _by_type(result, "risk")
    _accept(client, alice, decision["id"])
    _accept(client, alice, lesson["id"])

    # the source is edited: the lesson and the (unreviewed) risk are gone from it
    edited = (ARCH_NOTES.replace("Lesson learned: backups must be restore-tested every quarter. ", "")
              .replace("Risk: vendor lock-in with the cloud provider could be costly. ", "")
              + "Owner: the platform team.\n")
    r = client.put(f"/knowledge/artifacts/{result['artifact']['id']}", headers=alice, json={"content": edited})
    assert r.status_code == 200, r.text

    items = _items(client, alice)
    assert risk["id"] not in items                               # unreviewed: dropped as before
    assert lesson["id"] in items                                 # accepted: kept, flagged
    assert [s["kind"] for s in items[lesson["id"]]["health"]["signals"]] == ["removed_from_source"]
    # still in the source, but the source around it changed
    assert [s["kind"] for s in items[decision["id"]]["health"]["signals"]] == ["source_changed"]
    assert items[decision["id"]]["health"]["signals"][0]["source_title"] == "Architecture notes"
    # items that are still pending aren't flagged: they still wait for review
    assert all(not i["health"]["signals"] for i in items.values() if i["review_status"] == "pending")

    # the history says why
    events = _item(client, alice, lesson["id"])["events"]
    assert any(e["kind"] == "health_flagged" and e["detail"]["kind"] == "removed_from_source" for e in events)


def test_an_edited_decision_keeps_the_accepted_version_and_its_history(client, alice):
    """Rewording a decision in its source used to delete the accepted item along with its lineage."""
    result = ingest_text(client, alice)
    decision = _by_type(result, "decision")
    older = _decision(client, alice, "Old billing DB", "We decided to adopt MySQL for the billing database.")
    _link(client, alice, decision["id"], older["id"], "supersedes")

    reworded = ARCH_NOTES.replace("because the team knows it well", "because it supports JSON columns")
    client.put(f"/knowledge/artifacts/{result['artifact']['id']}", headers=alice, json={"content": reworded})

    items = _items(client, alice)
    assert decision["id"] in items                               # linked: kept, not deleted
    assert items[decision["id"]]["health"]["signals"][0]["kind"] == "removed_from_source"
    assert items[older["id"]]["status"] == "superseded"           # the history survived
    new = [i for i in items.values() if i["type"] == "decision" and i["artifact_id"] == result["artifact"]["id"]
           and i["id"] != decision["id"]]
    assert len(new) == 1 and new[0]["review_status"] == "pending"   # the reworded one waits for review


def test_reingesting_the_same_content_raises_nothing(client, alice):
    result = ingest_text(client, alice)
    decision = _by_type(result, "decision")
    _accept(client, alice, decision["id"])
    r = client.put(f"/knowledge/artifacts/{result['artifact']['id']}", headers=alice, json={"content": ARCH_NOTES})
    assert r.status_code == 200, r.text
    assert _items(client, alice)[decision["id"]]["health"]["state"] == "ok"


def test_a_reversed_decision_puts_its_amendment_in_doubt(client, alice):
    base = _decision(client, alice, "Billing DB", "We decided to adopt MySQL for the billing database.")
    amendment = _decision(client, alice, "Billing DB amendment",
                          "We decided to add read replicas to the MySQL billing database.")
    reversal = _decision(client, alice, "Billing DB reversal",
                         "We decided to drop MySQL for billing and move to PostgreSQL instead.")
    _link(client, alice, amendment["id"], base["id"], "amends")
    assert _items(client, alice)[amendment["id"]]["health"]["state"] == "ok"

    _link(client, alice, reversal["id"], base["id"], "reverses")
    signals = _items(client, alice)[amendment["id"]]["health"]["signals"]
    assert len(signals) == 1
    assert signals[0]["kind"] == "dependency_changed"
    assert signals[0]["reason"] == "The decision it amends was reversed"
    assert signals[0]["item_id"] == base["id"]
    # the reversed decision itself isn't asked about: its status already says it
    assert base["id"] not in {i["id"] for i in client.get("/knowledge/health", headers=alice).json()["items"]}


def test_risk_and_mitigation_signals(client, alice):
    risk = _by_type(ingest_text(client, alice, title="Risks",
                                content="Risk: the billing database could run out of connections."), "risk")
    fix = _decision(client, alice, "Pooling", "We decided to put PgBouncer in front of the billing database.")
    _link(client, alice, fix["id"], risk["id"], "mitigates")
    assert _items(client, alice)[risk["id"]]["status"] == "mitigated"

    # the mitigation is reversed: the risk may be back
    undo = _decision(client, alice, "No pooling", "We decided to remove PgBouncer from the billing stack.")
    _link(client, alice, undo["id"], fix["id"], "reverses")
    assert _items(client, alice)[risk["id"]]["health"]["signals"][0]["reason"] \
        == "A decision that mitigates it was reversed"

    # the risk happened anyway: the mitigation didn't hold
    incident = _by_type(ingest_text(client, alice, title="Outage",
                                    content="Lesson learned: billing ran out of database connections on Monday."),
                        "lesson")
    _link(client, alice, incident["id"], risk["id"], "realizes")
    # the mitigation itself is reversed (retired), so it isn't flagged again
    assert not _items(client, alice)[fix["id"]]["health"]["signals"]


def test_flag_verify_and_review_dates(client, alice):
    decision = _decision(client, alice, "Billing DB", "We decided to adopt MySQL for the billing database.")
    url = f"/knowledge/items/{decision['id']}"

    assert client.post(f"{url}/health/flag", headers=alice, json={"note": ""}).status_code == 422   # needs a reason
    r = client.post(f"{url}/health/flag", headers=alice, json={"note": "MySQL 5.7 is end of life"})
    assert r.status_code == 200, r.text
    flagged = r.json()["health"]
    assert flagged["state"] == "potentially_stale"
    assert flagged["signals"][0]["note"] == "MySQL 5.7 is end of life"
    assert flagged["signals"][0]["actor"]

    next_review = (date.today() + timedelta(days=180)).isoformat()
    r = client.post(f"{url}/health/verify", headers=alice, json={"note": "checked with the DBAs",
                                                                 "review_due": next_review})
    assert r.status_code == 200, r.text
    verified = r.json()["health"]
    assert verified["state"] == "ok" and not verified["signals"]
    assert verified["review_due"] == next_review and verified["verified_at"] and verified["verified_by"]

    soon = (date.today() + timedelta(days=3)).isoformat()
    r = client.put(f"{url}/review-date", headers=alice, json={"review_due": soon})
    assert r.status_code == 200 and r.json()["health"]["state"] == "needs_review"

    kinds = [e["kind"] for e in _item(client, alice, decision["id"])["events"]]
    assert kinds[-3:] == ["health_flagged", "verified", "review_date_set"]


def test_attention_list_orders_and_counts(client, alice):
    a = _decision(client, alice, "A", "We decided to adopt MySQL for the billing database.")
    b = _decision(client, alice, "B", "We decided to use Redis for the session cache.")
    c = _decision(client, alice, "C", "We decided to deploy on Fridays only.")
    _decision(client, alice, "D", "We decided to use trunk-based development.")   # healthy: not listed
    overdue = (date.today() - timedelta(days=2)).isoformat()
    client.put(f"/knowledge/items/{b['id']}/review-date", headers=alice, json={"review_due": overdue})
    client.post(f"/knowledge/items/{c['id']}/health/flag", headers=alice, json={"note": "deploys are daily now"})
    client.post(f"/knowledge/items/{a['id']}/health/flag", headers=alice, json={"note": "MySQL is end of life"})
    client.post(f"/knowledge/items/{a['id']}/health/flag", headers=alice, json={"note": "billing moved"})

    r = client.get("/knowledge/health", headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["id"] for i in body["items"]] == [a["id"], c["id"], b["id"]]   # most signals first, then dates
    assert body["counts"] == {"potentially_stale": 2, "needs_review": 1, "overdue": 1}
    assert body["total"] == 3 and r.headers["X-Total-Count"] == "3"
    assert [i["id"] for i in client.get("/knowledge/health?limit=1&offset=1", headers=alice).json()["items"]] \
        == [c["id"]]


def test_health_is_per_space(client, alice, bob):
    decision = _decision(client, alice, "Billing DB", "We decided to adopt MySQL for the billing database.")
    url = f"/knowledge/items/{decision['id']}"
    assert client.post(f"{url}/health/flag", headers=bob, json={"note": "not yours"}).status_code == 404
    assert client.post(f"{url}/health/verify", headers=bob, json={}).status_code == 404
    assert client.put(f"{url}/review-date", headers=bob, json={"review_due": None}).status_code == 404
    client.post(f"{url}/health/flag", headers=alice, json={"note": "outdated"})
    assert client.get("/knowledge/health", headers=bob).json()["total"] == 0


def test_graphrag_says_when_an_entry_may_be_out_of_date(client, alice, llm):
    decision = _decision(client, alice, "Billing DB", "We decided to adopt PostgreSQL for the billing database.")
    client.post(f"/knowledge/items/{decision['id']}/health/flag", headers=alice,
                json={"note": "billing is moving to a managed service"})
    llm.handlers["generate"] = lambda messages: "PostgreSQL [1]."
    r = client.post("/knowledge/graphrag/query", headers=alice,
                    json={"question": "What did we decide about PostgreSQL for billing?"})
    assert r.status_code == 200, r.text
    context = llm.last("generate")[-1]["content"]
    assert "may be out of date: " in context
    cited = r.json()["citations"]
    assert cited and cited[0]["health"] == "potentially_stale"
