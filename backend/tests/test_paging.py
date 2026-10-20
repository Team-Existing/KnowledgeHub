"""
Bounded responses: a large space used to come back whole (4 MB for 3,000 items),
which made pages slow and, under load, ran the database out of memory. Lists
are now pages with totals, and only the item page loads a document's text.
"""
from __future__ import annotations

import pytest

from tests.helpers import ARCH_NOTES, ingest_text


@pytest.fixture
def space(client, alice):
    """Three sources in alice's space, newest last."""
    for n in range(3):
        ingest_text(client, alice, title=f"Notes {n}", content=ARCH_NOTES.replace("PostgreSQL", f"PostgreSQL {n}"))
    return alice


def test_the_hub_gets_a_page_with_totals_and_no_document_text(client, space):
    full = client.get("/knowledge", headers=space).json()
    totals = full["totals"]
    assert totals["artifacts"] == 3 and totals["knowledge_items"] == len(full["knowledge_items"]) > 3
    assert totals["pending_review"] == totals["knowledge_items"]
    assert all("content" not in a for a in full["artifacts"])
    assert "decision" in full["item_types"]

    page = client.get("/knowledge", headers=space, params={"limit": 2}).json()
    assert [a["title"] for a in page["artifacts"]] == ["Notes 2", "Notes 1"]          # newest first
    assert len(page["knowledge_items"]) == 2 and page["totals"] == totals
    shown = {a["id"] for a in page["artifacts"]} | {i["id"] for i in page["knowledge_items"]}
    assert all(r["from"] in shown and r["to"] in shown for r in page["relationships"])

    rest = client.get("/knowledge", headers=space, params={"limit": 2, "offset": 2}).json()
    assert [a["title"] for a in rest["artifacts"]] == ["Notes 0"]
    seen = {i["id"] for i in page["knowledge_items"]} | {i["id"] for i in rest["knowledge_items"]}
    assert len(seen) == 4                                                              # no overlap between pages

    with_text = client.get("/knowledge", headers=space, params={"include_content": True}).json()
    assert all("PostgreSQL" in a["content"] for a in with_text["artifacts"])
    assert client.get("/knowledge", headers=space, params={"limit": 5000}).status_code == 422


def test_the_hub_filters_on_the_server(client, space):
    risks = client.get("/knowledge", headers=space, params={"type": "risk"}).json()
    assert risks["knowledge_items"] and {i["type"] for i in risks["knowledge_items"]} == {"risk"}
    assert risks["totals"]["matching_items"] == len(risks["knowledge_items"]) < risks["totals"]["knowledge_items"]
    hits = client.get("/knowledge", headers=space, params={"q": "postgresql 1"}).json()["knowledge_items"]
    assert [i["title"] for i in hits] and all("PostgreSQL 1" in i["title"] for i in hits)
    tagged = client.get("/knowledge", headers=space, params={"q": "ARCH"}).json()   # tags too, any case
    assert len(tagged["knowledge_items"]) == tagged["totals"]["knowledge_items"]


def test_items_endpoint_filters_by_several_types(client, space):
    body = client.get("/knowledge/items", headers=space,
                      params={"type": ["how-to", "checklist"], "exclude_rejected": True}).json()
    assert body["items"] and {i["type"] for i in body["items"]} <= {"how-to", "checklist"}
    assert body["total"] == len(body["items"])
    assert client.get("/knowledge/items", headers=space, params={"limit": 1}).json()["total"] > 1


def test_the_item_page_gets_its_context(client, space):
    item = client.get("/knowledge/items", headers=space, params={"type": "decision", "limit": 1}).json()["items"][0]
    ctx = client.get(f"/knowledge/items/{item['id']}/context", headers=space).json()
    assert ctx["item"]["id"] == item["id"]
    assert ctx["artifact"]["id"] == item["artifact_id"] and "PostgreSQL" in ctx["artifact"]["content"]
    assert {"from": item["artifact_id"], "to": item["id"], "type": "CONTAINS"} in ctx["relationships"]
    assert ctx["nodes"][item["artifact_id"]]["kind"] == "artifact"
    assert client.get("/knowledge/items/nope/context", headers=space).status_code == 404


def test_another_space_gets_no_context(client, space, bob):
    item = client.get("/knowledge/items", headers=space, params={"limit": 1}).json()["items"][0]
    assert client.get(f"/knowledge/items/{item['id']}/context", headers=bob).status_code == 404


def test_the_review_queue_is_paged(client, space):
    r = client.get("/knowledge/review", headers=space, params={"limit": 3})
    assert r.status_code == 200 and len(r.json()) == 3
    assert int(r.headers["X-Total-Count"]) > 3


def test_the_graph_is_capped_and_says_so(client, space):
    full = client.get("/knowledge/graph", headers=space).json()
    assert full["truncated"] is False and full["total_nodes"] == len(full["nodes"])
    capped = client.get("/knowledge/graph", headers=space, params={"limit": 4}).json()
    assert len(capped["nodes"]) == 4 and capped["truncated"] and capped["total_nodes"] == full["total_nodes"]
    ids = {n["id"] for n in capped["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in capped["edges"])


def test_the_register_is_paged_and_names_items_outside_the_page(client, space):
    decisions = client.get("/knowledge/register", headers=space).json()
    assert decisions["total"] == 3 == len(decisions["items"])
    newest, older = decisions["items"][0], decisions["items"][2]
    r = client.post(f"/knowledge/items/{newest['id']}/lineage", headers=space,
                    json={"target_id": older["id"], "kind": "supersedes"})
    assert r.status_code == 201, r.text
    page = client.get("/knowledge/register", headers=space, params={"limit": 1}).json()
    assert [i["id"] for i in page["items"]] == [newest["id"]] and page["total"] == 3
    assert older["id"] in {i["id"] for i in page["related"]}       # the chain still has a title


def test_search_results_carry_no_document_text_but_link_somewhere(client, space):
    body = client.get("/knowledge/search", headers=space, params={"q": "migrations"}).json()
    assert body["artifacts"]
    for a in body["artifacts"]:
        assert "content" not in a
        assert a["first_item_id"].startswith(("decisions_", "risks_", "lessons_", "best", "how", "check"))
    item = client.get(f"/knowledge/items/{body['artifacts'][0]['first_item_id']}", headers=space).json()
    assert item["artifact_id"] == body["artifacts"][0]["id"]


def test_the_summary_is_built_after_the_response(client, alice, llm, db_query):
    # through the route: the summary is a background task, which the test client runs before returning
    llm.handlers["summary"] = lambda m: "A short summary of the billing notes."
    body = ingest_text(client, alice, title="Deferred")
    art = db_query("SELECT summary FROM Artifact WHERE id = :id", id=body["artifact"]["id"])[0]
    assert art["summary"] == "A short summary of the billing notes."


def test_the_pipeline_schedules_the_summary_instead_of_waiting(llm):
    import asyncio
    import app.services.artifact_pipeline as pipeline
    scheduled = []

    class Recorder:                     # stands in for FastAPI's BackgroundTasks
        def add_task(self, fn, *args):
            scheduled.append((fn.__name__, args))

    # no items: nothing to embed, so no database is needed
    asyncio.run(pipeline.index_artifact(None, "space-1", {"id": "artifact_x", "content": "text"}, [],
                                        background=Recorder()))
    assert [name for name, _ in scheduled] == ["summarise_later"]
    assert scheduled[0][1][:3] == ("space-1", "artifact_x", "text")
    assert "summary" not in llm.kinds()             # nothing waited on the LLM
