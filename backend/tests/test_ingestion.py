"""Ingestion: manual text, transcripts, files, URLs, edits/deletes, OKF."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.helpers import ARCH_NOTES, ingest_text, minimal_docx, minimal_pdf, upload


def items_of(client, headers, artifact_id):
    data = client.get("/knowledge", headers=headers).json()
    return [i for i in data["knowledge_items"] if i["artifact_id"] == artifact_id]


# ── manual text (regex extraction) ──────────────────────────────────────────

def test_text_ingest_extracts_typed_items(client, alice):
    result = ingest_text(client, alice)
    types = {i["type"] for i in result["items"]}
    assert {"decision", "best-practice", "lesson", "risk", "how-to", "checklist"} <= types
    assert result["extracted_count"] == len(result["items"])
    assert sorted(i["title"] for i in result["items"] if i["type"] == "checklist") == ["notify on-call", "update the runbook"]

    stored = items_of(client, alice, result["artifact"]["id"])
    assert {i["id"] for i in stored} == {i["id"] for i in result["items"]}
    assert all(i["review_status"] == "pending" and i["extraction_engine"] == "regex" for i in stored)
    # every item hangs off its artifact with a CONTAINS edge
    rels = client.get("/knowledge", headers=alice).json()["relationships"]
    contains = {r["to"] for r in rels if r["from"] == result["artifact"]["id"] and r["type"] == "CONTAINS"}
    assert contains == {i["id"] for i in result["items"]}


def test_reingest_is_idempotent_and_keeps_review_state(client, alice):
    first = ingest_text(client, alice)
    item_id = first["items"][0]["id"]
    r = client.patch(f"/knowledge/review/{item_id}", headers=alice, json={"status": "accepted", "note": "ok"})
    assert r.status_code == 200

    second = ingest_text(client, alice, tags=("arch", "v2"))
    assert second["artifact"]["id"] == first["artifact"]["id"]
    assert {i["id"] for i in second["items"]} == {i["id"] for i in first["items"]}
    assert len(items_of(client, alice, first["artifact"]["id"])) == len(first["items"])
    assert client.get(f"/knowledge/items/{item_id}", headers=alice).json()["review_status"] == "accepted"


def test_items_are_embedded_on_ingest(client, alice, db_query):
    art = ingest_text(client, alice)["artifact"]
    rows = db_query("SELECT embedding, embedding_provider, embedding_dims FROM KnowledgeItem WHERE artifact_id = :a",
                    a=art["id"])
    assert rows and all(r["embedding"] and len(r["embedding"]) == r["embedding_dims"] == 64 for r in rows)
    assert all(r["embedding_provider"] == "fake:bow" for r in rows)
    summary = db_query("SELECT summary, summary_embedding FROM Artifact WHERE id = :a", a=art["id"])[0]
    assert summary["summary"] and len(summary["summary_embedding"]) == 64


def test_ingest_is_stored_as_a_graph(client, alice, db_query):
    result = ingest_text(client, alice)
    art_id = result["artifact"]["id"]
    contained = db_query("SELECT expand(out('CONTAINS')) FROM Artifact WHERE id = :a", a=art_id)
    assert {r["id"] for r in contained} == {i["id"] for i in result["items"]}


def test_failed_write_rolls_back_the_whole_ingest(client, alice, db_query, monkeypatch):
    import app.repositories.relationships as rel_module
    from app.arcadedb import ArcadeDBError

    async def boom(self, user_id, artifact_id, item_ids):
        raise ArcadeDBError(500, "Test", "edge creation failed")
    monkeypatch.setattr(rel_module.RelationshipRepository, "ensure_contains", boom)
    title = "Rollback check"
    with __import__("pytest").raises(ArcadeDBError):
        client.post("/knowledge/artifacts", headers=alice,
                    json={"title": title, "content": "We decided to test transactions."})
    # artifact + items were written in the same transaction, so none of it was kept
    assert db_query("SELECT count(*) AS n FROM Artifact WHERE title = :t", t=title)[0]["n"] == 0


def test_content_length_limit_on_json_ingest(client, alice):
    r = client.post("/knowledge/artifacts", headers=alice, json={"title": "t", "content": "x" * 100_001})
    assert r.status_code == 422


# ── transcripts (LLM extraction) ────────────────────────────────────────────

TRANSCRIPT_JSON = json.dumps({
    "summary": "Team agreed to launch Monday.",
    "decisions": [{"what": "Launch on Monday", "why": "QA signed off", "who": "Ana"}],
    "action_items": [{"task": "Write release notes", "owner": "Ben", "due": "Friday"}],
    "risks": [{"risk": "Payment provider outage", "severity": "high"}],
})


def test_transcript_uses_llm_extraction(client, alice, llm):
    llm.handlers["transcript"] = lambda m: TRANSCRIPT_JSON
    r = client.post("/knowledge/artifacts/transcript", headers=alice, json={
        "title": "Standup", "content": "Ana: let's launch Monday. Ben: I'll write the notes.", "source_type": "transcript"})
    assert r.status_code == 200
    body = r.json()
    by_type = {i["type"]: i for i in body["items"]}
    assert set(by_type) == {"decision", "action-item", "risk"}
    assert by_type["action-item"]["title"] == "Write release notes"
    assert by_type["action-item"]["author"] == "Ben"             # "owner" becomes the author
    assert by_type["risk"]["details"]["severity"] == "high"
    assert body["summary"] == "Team agreed to launch Monday." and body["llm_error"] is None
    stored = items_of(client, alice, body["artifact"]["id"])
    assert all(i["extraction_engine"] == "local_llm" for i in stored)


def test_transcript_falls_back_to_regex_without_llm(client, alice, llm):
    r = client.post("/knowledge/artifacts/transcript", headers=alice, json={
        "title": "Standup 2",
        "content": "We agreed to ship on Friday. Ben needs to update the docs before then."})
    body = r.json()
    assert r.status_code == 200 and "regex fallback" in body["llm_error"]
    types = {i["type"] for i in body["items"]}
    assert "decision" in types
    assert "action-item" in types      # regression: fallback action items used to be dropped
    assert all(i["extraction_engine"] == "regex" for i in items_of(client, alice, body["artifact"]["id"]))


def test_transcript_with_malformed_llm_json_falls_back(client, alice, llm):
    llm.handlers["transcript"] = lambda m: "Sure! Here are the decisions: {not json"
    r = client.post("/knowledge/artifacts/transcript", headers=alice, json={
        "title": "Standup 3", "content": "We decided to cancel the offsite."})
    assert r.status_code == 200
    assert r.json()["llm_error"]
    assert any(i["type"] == "decision" for i in r.json()["items"])


# ── files ───────────────────────────────────────────────────────────────────

DOC_JSON = json.dumps({
    "decisions": [{"what": "Adopt PostgreSQL for billing", "why": "", "who": ""}],
    "action_items": [], "how_tos": [], "best_practices": [], "lessons": [],
    "risks": [{"risk": "Vendor lock-in", "severity": "medium"}],
})


@pytest.mark.parametrize("filename,payload", [
    ("notes.txt", b"We decided to adopt PostgreSQL. Risk: vendor lock-in could be costly."),
    ("notes.md", b"# Notes\n\nWe decided to adopt PostgreSQL. Risk: vendor lock-in could be costly."),
    ("notes.pdf", minimal_pdf("We decided to adopt PostgreSQL. Risk: vendor lock-in could be costly.")),
    ("notes.docx", minimal_docx("We decided to adopt PostgreSQL.", "Risk: vendor lock-in could be costly.")),
], ids=["txt", "md", "pdf", "docx"])
def test_file_upload_parses_each_format(client, alice, filename, payload):
    r = upload(client, alice, filename, payload, title=f"File {filename}", tags="a, b")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "PostgreSQL" in body["artifact"]["content"]
    assert body["artifact"]["tags"] == ["a", "b"]
    assert {"decision", "risk"} <= {i["type"] for i in body["items"]}


def test_file_upload_uses_llm_when_available(client, alice, llm):
    llm.handlers["document"] = lambda m: DOC_JSON
    content = ARCH_NOTES.encode()
    body = upload(client, alice, "arch.txt", content, title="LLM doc").json()
    by_engine = {}
    for i in body["items"]:
        by_engine.setdefault(i["extraction_engine"], set()).add(i["type"])
    assert by_engine["local_llm"] == {"decision", "risk"}
    assert by_engine["regex"] == {"checklist"}            # checklists always come from regex
    assert "document" in llm.kinds()
    stored = {i["id"]: i["extraction_engine"] for i in items_of(client, alice, body["artifact"]["id"])}
    assert all(stored[i["id"]] == i["extraction_engine"] for i in body["items"])


def test_file_upload_without_llm_uses_regex(client, alice, llm):
    body = upload(client, alice, "arch2.txt", ARCH_NOTES.encode(), title="Regex doc").json()
    assert "regex fallback" in body["llm_error"]
    assert all(i["details"].get("extractor", "regex") == "regex" for i in body["items"])
    assert len(body["items"]) >= 6


@pytest.mark.parametrize("filename,status,fragment", [
    ("legacy.doc", 415, ".docx"),
    ("sheet.xlsx", 415, "Unsupported file type"),
    ("broken.pdf", 422, "Could not parse PDF"),
    ("broken.docx", 422, "Could not parse DOCX"),
])
def test_file_upload_rejections(client, alice, filename, status, fragment):
    r = upload(client, alice, filename, b"not really a document")
    assert r.status_code == status
    assert fragment in r.json()["detail"]


def test_oversized_upload_is_rejected(client, alice):
    r = upload(client, alice, "big.txt", b"a" * (1024 * 1024 + 200 * 1024))   # MAX_UPLOAD_MB=1 in tests
    assert r.status_code == 413
    assert "MB limit" in r.json()["detail"]


# ── URLs ────────────────────────────────────────────────────────────────────

def test_url_ingest_blocks_private_addresses(client, alice):
    for url in ("http://127.0.0.1:8000/", "http://localhost/", "http://169.254.169.254/latest/", "file:///etc/passwd"):
        r = client.post("/knowledge/artifacts/url", headers=alice, json={"url": url, "title": "x"})
        assert r.status_code == 400, url


@pytest.fixture
def page_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/page")
                self.end_headers()
                return
            body = b"We decided to ship weekly releases. Risk: on-call burnout is likely."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_url_ingest_fetches_and_follows_redirects(client, alice, page_server, monkeypatch):
    import app.services.file_ingestion as fi

    async def allow(url):   # the test server is on loopback, which is normally blocked
        return None
    monkeypatch.setattr(fi, "_assert_public_host", allow)
    r = client.post("/knowledge/artifacts/url", headers=alice, json={"url": f"{page_server}/moved", "title": "Page"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ship weekly" in body["artifact"]["content"]
    assert body["artifact"]["source"] == f"{page_server}/moved"
    assert {"decision", "risk"} <= {i["type"] for i in body["items"]}


# ── edit / delete ───────────────────────────────────────────────────────────

def test_editing_content_reextracts_items(client, alice):
    art = ingest_text(client, alice)["artifact"]
    r = client.put(f"/knowledge/artifacts/{art['id']}", headers=alice,
                   json={"content": "We decided to use Redis for caching. Risk: cache stampede could overload the DB."})
    assert r.status_code == 200
    titles = {i["title"] for i in items_of(client, alice, art["id"])}
    assert any("Redis" in t for t in titles)
    assert not any("PostgreSQL" in t for t in titles)          # stale items removed


def test_deleting_artifact_cascades(client, alice):
    art = ingest_text(client, alice, title="Doomed", content="We decided to sunset the zanzibar service.")["artifact"]
    assert client.get("/knowledge/search", headers=alice, params={"q": "zanzibar"}).json()["total"] > 0
    assert client.delete(f"/knowledge/artifacts/{art['id']}", headers=alice).status_code == 204
    assert items_of(client, alice, art["id"]) == []
    data = client.get("/knowledge", headers=alice).json()
    assert not any(r["from"] == art["id"] for r in data["relationships"])
    assert client.get("/knowledge/search", headers=alice, params={"q": "zanzibar"}).json()["total"] == 0


def test_deleting_item_removes_its_edges(client, alice):
    item = ingest_text(client, alice)["items"][0]
    assert client.delete(f"/knowledge/items/{item['id']}", headers=alice).status_code == 204
    assert client.get(f"/knowledge/items/{item['id']}", headers=alice).status_code == 404
    rels = client.get("/knowledge", headers=alice).json()["relationships"]
    assert not any(item["id"] in (r["from"], r["to"]) for r in rels)


# ── OKF ─────────────────────────────────────────────────────────────────────

def test_okf_export_import_roundtrip(client, alice, bob):
    ingest_text(client, alice)
    exported = client.get("/knowledge/okf/export", headers=alice).json()
    assert exported["format"] == "okf" and exported["nodes"]
    r = client.post("/knowledge/okf/import", headers=bob, json=exported)
    assert r.status_code == 200
    body = r.json()
    assert body["imported_items"] == len(exported["nodes"])
    bob_view = client.get("/knowledge", headers=bob).json()
    assert {n["name"] for n in exported["nodes"]} <= {i["title"] for i in bob_view["knowledge_items"]}
    # imported items belong to the import artifact and are embedded like any other item
    import_artifact = body["artifact"]["id"]
    imported = [i for i in bob_view["knowledge_items"] if i["artifact_id"] == import_artifact]
    contains = {r["to"] for r in bob_view["relationships"] if r["from"] == import_artifact and r["type"] == "CONTAINS"}
    assert {i["id"] for i in imported} <= contains
    # the export's edges point at alice's artifact ids, which bob doesn't have: skipped, not dangling
    assert body["relationships"] + body["relationships_skipped"] == len(exported["edges"])


def test_okf_import_links_imported_nodes(client, alice, db_query):
    payload = {"title": "Graph import", "nodes": [
        {"id": "n1", "name": "Adopt PostgreSQL", "kind": "decision"},
        {"id": "n2", "name": "Vendor lock-in risk", "kind": "risk"},
    ], "edges": [{"source": "n1", "target": "n2", "type": "MITIGATES"},
                 {"source": "n1", "target": "missing", "type": "X"}]}
    body = client.post("/knowledge/okf/import", headers=alice, json=payload).json()
    assert body["relationships"] == 1 and body["relationships_skipped"] == 1
    rels = client.get("/knowledge", headers=alice).json()["relationships"]
    assert any(r["type"] == "MITIGATES" for r in rels)
    imported = db_query("SELECT id, embedding FROM KnowledgeItem WHERE artifact_id = :a AND id LIKE 'okf_%'",
                        a=body["artifact"]["id"])
    assert len(imported) == 2 and all(r["embedding"] for r in imported)
