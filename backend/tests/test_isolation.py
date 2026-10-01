"""Per-user isolation: nothing one user owns is visible to or changeable by another."""
from __future__ import annotations

import pytest

from tests.helpers import ingest_text


@pytest.fixture
def alice_data(client, alice):
    result = ingest_text(client, alice, title="Alice secrets",
                         content="We decided to acquire zanzibarcorp next quarter. Risk: leak of the zanzibarcorp deal.")
    return result


def test_unauthenticated_requests_are_rejected(client):
    for method, path in [("GET", "/knowledge"), ("GET", "/knowledge/search"), ("POST", "/knowledge/graphrag/query"),
                         ("GET", "/knowledge/graph"), ("GET", "/knowledge/review")]:
        assert client.request(method, path).status_code == 401, path
    r = client.get("/knowledge", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_listing_shows_only_own_data(client, alice, bob, alice_data):
    ingest_text(client, bob, title="Bob notes", content="We decided to paint the office green.")
    bob_view = client.get("/knowledge", headers=bob).json()
    alice_ids = {i["id"] for i in alice_data["items"]} | {alice_data["artifact"]["id"]}
    seen = {i["id"] for i in bob_view["knowledge_items"]} | {a["id"] for a in bob_view["artifacts"]} \
        | {r["from"] for r in bob_view["relationships"]} | {r["to"] for r in bob_view["relationships"]}
    assert not (alice_ids & seen)
    assert [a["title"] for a in bob_view["artifacts"]] == ["Bob notes"]


def test_same_content_gets_distinct_ids_per_user(client, alice, bob):
    a = ingest_text(client, alice, title="Shared doc")
    b = ingest_text(client, bob, title="Shared doc")
    assert a["artifact"]["id"] != b["artifact"]["id"]
    assert not ({i["id"] for i in a["items"]} & {i["id"] for i in b["items"]})
    # bob ingesting identical content must not touch alice's rows
    assert len(client.get("/knowledge", headers=alice).json()["knowledge_items"]) == len(a["items"])


def test_other_users_items_are_not_found(client, bob, alice_data):
    item_id = alice_data["items"][0]["id"]
    artifact_id = alice_data["artifact"]["id"]
    assert client.get(f"/knowledge/items/{item_id}", headers=bob).status_code == 404
    assert client.put(f"/knowledge/items/{item_id}", headers=bob, json={"title": "pwned"}).status_code == 404
    assert client.delete(f"/knowledge/items/{item_id}", headers=bob).status_code == 404
    assert client.patch(f"/knowledge/review/{item_id}", headers=bob, json={"status": "rejected"}).status_code == 404
    assert client.put(f"/knowledge/artifacts/{artifact_id}", headers=bob, json={"title": "pwned"}).status_code == 404
    assert client.delete(f"/knowledge/artifacts/{artifact_id}", headers=bob).status_code == 404


def test_loose_lookup_does_not_leak(client, bob, alice_data):
    item = alice_data["items"][0]
    for ref in (item["id"], item["id"].split("_")[-1], "zanzibarcorp"):
        assert client.get(f"/knowledge/{ref}", headers=bob).status_code == 404, ref


def test_failed_cross_user_writes_change_nothing(client, alice, bob, alice_data):
    item_id = alice_data["items"][0]["id"]
    client.put(f"/knowledge/items/{item_id}", headers=bob, json={"title": "pwned"})
    client.delete(f"/knowledge/artifacts/{alice_data['artifact']['id']}", headers=bob)
    item = client.get(f"/knowledge/items/{item_id}", headers=alice).json()
    assert item["title"] == alice_data["items"][0]["title"]
    assert len(client.get("/knowledge", headers=alice).json()["knowledge_items"]) == len(alice_data["items"])


def test_search_is_scoped(client, alice, bob, alice_data):
    assert client.get("/knowledge/search", headers=alice, params={"q": "zanzibarcorp"}).json()["total"] > 0
    r = client.get("/knowledge/search", headers=bob, params={"q": "zanzibarcorp"}).json()
    assert r["total"] == 0 and r["knowledge_items"] == [] and r["artifacts"] == []
    assert client.get("/knowledge/search", headers=bob).json()["total"] == 0          # no query: still nothing


def test_review_queue_graph_and_export_are_scoped(client, bob, alice_data):
    assert client.get("/knowledge/review", headers=bob).json() == []
    graph = client.get("/knowledge/graph", headers=bob).json()
    assert graph["nodes"] == [] and graph["edges"] == []
    export = client.get("/knowledge/okf/export", headers=bob).json()
    assert export["nodes"] == [] and export["edges"] == []


def test_cross_links_are_scoped(client, alice, bob, alice_data):
    ingest_text(client, alice, title="Alice follow-up", content="We decided to announce the zanzibarcorp deal.")
    ingest_text(client, bob, title="Bob", content="We decided to announce the zanzibarcorp deal as well.")
    client.post("/knowledge/link", headers=alice)
    client.post("/knowledge/link", headers=bob)
    alice_items = {i["id"] for i in client.get("/knowledge", headers=alice).json()["knowledge_items"]}
    bob_items = {i["id"] for i in client.get("/knowledge", headers=bob).json()["knowledge_items"]}
    for link in client.get("/knowledge/links", headers=bob).json():
        assert {link["item_id_a"], link["item_id_b"]} <= bob_items
    alice_links = client.get("/knowledge/links", headers=alice).json()
    assert alice_links, "expected alice's two artifacts to be cross-linked"
    for link in alice_links:
        assert {link["item_id_a"], link["item_id_b"]} <= alice_items


def test_graphrag_only_retrieves_own_items(client, bob, alice_data, llm):
    r = client.post("/knowledge/graphrag/query", headers=bob, json={"question": "What did we decide about zanzibarcorp?"})
    assert r.status_code == 200
    body = r.json()
    assert body["context_nodes"] == [] and body["citations"] == []
    assert "No relevant knowledge" in body["answer"]
    assert "generate" not in llm.kinds()


def test_deleting_artifact_leaves_other_users_data(client, alice, bob, alice_data):
    # bob imports alice's export: same titles, but his own nodes and edges
    exported = client.get("/knowledge/okf/export", headers=alice).json()
    client.post("/knowledge/okf/import", headers=bob, json=exported)
    bob_before = client.get("/knowledge", headers=bob).json()
    assert bob_before["relationships"] and bob_before["knowledge_items"]

    assert client.delete(f"/knowledge/artifacts/{alice_data['artifact']['id']}", headers=alice).status_code == 204
    assert client.get("/knowledge", headers=bob).json() == bob_before


def test_edges_never_cross_users(client, alice, bob, alice_data, db_query):
    ingest_text(client, bob, title="Bob", content="We decided to announce the zanzibarcorp deal as well.")
    for edge_type in ("CONTAINS", "RELATED_TO", "LINK"):
        crossing = db_query(f"SELECT count(*) AS n FROM {edge_type} "
                            "WHERE @out.user_id <> @in.user_id OR @out.user_id <> user_id")
        assert crossing[0]["n"] == 0, edge_type


def test_vector_search_finds_a_small_users_items_among_a_large_users(client, alice, bob, db_query):
    """
    The vector index is shared by all users. When another user's vectors fill
    every over-fetched ANN candidate slot, the small user must still get their
    own nearest items (exact-scan fallback), not an empty result.
    """
    import asyncio, os
    from app.arcadedb import ArcadeClient, ArcadeSession
    from app.repositories import GraphStore
    from tests.conftest import bag_of_words_vector

    crowd = " ".join(f"We decided to adopt postgres cluster variant {n} for billing." for n in range(150))
    ingest_text(client, bob, title="Bob crowd", content=crowd)
    mine = ingest_text(client, alice, title="Alice db", content="We decided to adopt postgres for billing.")
    bob_items = db_query("SELECT count(*) AS n FROM KnowledgeItem WHERE title LIKE '%variant%'")[0]["n"]
    assert bob_items > 100          # more than the ANN candidate count

    async def search():
        store_client = ArcadeClient(os.environ["ARCADEDB_URL"], os.environ["ARCADEDB_DATABASE"],
                                    os.environ["ARCADEDB_USER"], os.environ["ARCADEDB_PASSWORD"])
        try:
            store = GraphStore(ArcadeSession(store_client))
            vec = bag_of_words_vector("We decided to adopt postgres for billing")
            alice_id = mine["items"][0]["id"]
            user_id = db_query("SELECT user_id FROM KnowledgeItem WHERE id = :i", i=alice_id)[0]["user_id"]
            return await store.vector_search(user_id, vec, top_k=8), alice_id
        finally:
            await store_client.aclose()

    hits, alice_id = asyncio.run(search())
    assert [h["id"] for h in hits] == [alice_id]           # found, and nothing of bob's leaks in
    assert hits[0]["score"] > 0.9
