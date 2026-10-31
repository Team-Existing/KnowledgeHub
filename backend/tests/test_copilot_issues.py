"""Decision Copilot on Jira and Linear issues: fetch, comment once and update, webhooks and their verification."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Dict, List

import httpx
import pytest

from app.services.copilot_targets import jira_wiki, markdown

JIRA = "https://acme.atlassian.net"
LINEAR = "https://api.linear.app/graphql"
MEETING = json.dumps({
    "summary": "Payments stay on PostgreSQL.",
    "decisions": [{"what": "Use PostgreSQL for the payment service", "who": "Ana",
                   "why": "payments need transactional consistency"}],
    "action_items": [], "risks": [],
})
CONFLICT = json.dumps({"assessments": [{"n": 1, "relation": "conflicts", "reason": "MongoDB drops the transactions payments rely on"}]})


class FakeTrackers:
    """Jira issue ARCH-12 and Linear issue ENG-42, both about moving payments to MongoDB; records comments."""

    def __init__(self) -> None:
        self.jira_comments: List[Dict] = []
        self.linear_comments: List[Dict] = []
        self.description = "Move the payment service from PostgreSQL to MongoDB."
        self.done = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.atlassian.net":
            return self.jira(request)
        if request.url.host == "api.linear.app":
            return self.linear(request)
        return httpx.Response(404)

    def jira(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/rest/api/2/issue/ARCH-12" and method == "GET":
            return httpx.Response(200, json={"key": "ARCH-12", "fields": {
                "summary": "Move payments to MongoDB", "description": self.description,
                "status": {"statusCategory": {"key": "done" if self.done else "indeterminate"}}}})
        if path == "/rest/api/2/issue/ARCH-12/comment" and method == "POST":
            c = {"id": str(10000 + len(self.jira_comments)), "body": json.loads(request.content)["body"]}
            self.jira_comments.append(c)
            return httpx.Response(201, json=c)
        if path.startswith("/rest/api/2/issue/ARCH-12/comment/") and method == "PUT":
            cid = path.rsplit("/", 1)[1]
            c = next((c for c in self.jira_comments if c["id"] == cid), None)
            if not c:
                return httpx.Response(404, json={"errorMessages": ["Can not find a comment"]})
            c["body"] = json.loads(request.content)["body"]
            return httpx.Response(200, json=c)
        return httpx.Response(404, json={"errorMessages": [f"no fake for {method} {path}"]})

    def linear(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "lin_api_secret"
        body = json.loads(request.content)
        query, variables = body["query"], body["variables"]
        if "issue(id:" in query:
            if variables["id"] != "ENG-42":
                return httpx.Response(200, json={"data": {"issue": None}})
            return httpx.Response(200, json={"data": {"issue": {
                "id": "uuid-42", "identifier": "ENG-42", "title": "Move payments to MongoDB",
                "description": self.description, "url": "https://linear.app/acme/issue/ENG-42",
                "completedAt": "2026-01-01" if self.done else None, "canceledAt": None}}})
        if "commentCreate" in query:
            assert variables["input"]["issueId"] == "uuid-42"
            c = {"id": f"cmt-{len(self.linear_comments) + 1}", "body": variables["input"]["body"],
                 "url": f"https://linear.app/acme/issue/ENG-42#comment-{len(self.linear_comments) + 1}"}
            self.linear_comments.append(c)
            return httpx.Response(200, json={"data": {"commentCreate": {"success": True, "comment": c}}})
        if "commentUpdate" in query:
            c = next((c for c in self.linear_comments if c["id"] == variables["id"]), None)
            if not c:
                return httpx.Response(200, json={"errors": [{"message": "Entity not found"}]})
            c["body"] = variables["input"]["body"]
            return httpx.Response(200, json={"data": {"commentUpdate": {"success": True, "comment": c}}})
        return httpx.Response(400, json={"errors": [{"message": "unexpected query"}]})


@pytest.fixture
def trackers(mock_http):
    fake = FakeTrackers()
    fake.requests = mock_http(fake)
    return fake


@pytest.fixture
def connectors(client, alice, llm, trackers) -> Dict:
    """Jira and Linear connectors, the Payments system, a decision about it, and a model that sees a conflict."""
    jira = client.post("/connectors", headers=alice, json={"kind": "jira", "name": "Jira", "config": {
        "base_url": JIRA, "email": "bot@acme.test", "api_token": "jira-token"}}).json()
    linear = client.post("/connectors", headers=alice, json={"kind": "linear", "name": "Linear", "config": {
        "api_url": LINEAR, "api_key": "lin_api_secret"}}).json()
    client.post("/knowledge/systems", headers=alice, json={"name": "Payments", "aliases": ["payment service"]})
    llm.handlers["transcript"] = lambda m: MEETING
    client.post("/knowledge/artifacts/transcript", headers=alice, json={
        "title": "Architecture review", "content": "Ana: payments stay on PostgreSQL.", "source_type": "transcript"})
    llm.handlers["impact"] = lambda m: CONFLICT
    trackers.requests.clear()
    return {"jira": jira, "linear": linear}


def post(client, headers, connector, ref):
    r = client.post("/knowledge/copilot/post", headers=headers, json={"connector_id": connector["id"], "ref": ref})
    assert r.status_code == 200, r.text
    return r.json()


# ── rendering ───────────────────────────────────────────────────────────────

def test_one_comment_two_markups():
    blocks = [("heading", "Decisions"), ("warning", "May conflict"), ("bold", "Decisions on record"),
              ("item", "Use [PostgreSQL] | always", "https://hub/knowledge/d1", ["active, may conflict"]),
              ("item", "Unlinked", None, []), ("line", "**Systems:** Payments"), ("note", "Advisory only")]
    md = markdown(blocks)
    assert md.startswith("<!-- knowledge-hubs-copilot -->\n### Decisions")
    assert "- [Use [PostgreSQL] | always](https://hub/knowledge/d1)" in md and "  - active, may conflict" in md
    wiki = jira_wiki(blocks)
    assert wiki.startswith("h3. Decisions") and "{panel:bgColor=#fffae6}May conflict{panel}" in wiki
    assert "* [Use (PostgreSQL) / always|https://hub/knowledge/d1]" in wiki      # link syntax kept intact
    assert "** active, may conflict" in wiki and "* *Unlinked*" in wiki and "*Systems:* Payments" in wiki


# ── Jira ────────────────────────────────────────────────────────────────────

def test_jira_issue_gets_a_wiki_markup_comment_then_an_update(client, alice, connectors, trackers):
    preview = client.post("/knowledge/copilot/preview", headers=alice,
                          json={"connector_id": connectors["jira"]["id"], "ref": "arch-12"}).json()
    assert preview["target"]["ref"] == "ARCH-12" and preview["target"]["noun"] == "issue"
    assert preview["target"]["url"] == f"{JIRA}/browse/ARCH-12" and preview["relevant"] is True
    assert preview["comment"].startswith("h3. Knowledge Hubs: decisions this issue touches")

    first = post(client, alice, connectors["jira"], "ARCH-12")
    assert first["status"] == "posted" and first["comment_url"] == f"{JIRA}/browse/ARCH-12?focusedCommentId=10000"
    assert "may conflict" in trackers.jira_comments[0]["body"]
    second = post(client, alice, connectors["jira"], "ARCH-12")
    assert second["status"] == "updated" and len(trackers.jira_comments) == 1
    # someone deleted it: the next one is posted afresh
    trackers.jira_comments.clear()
    assert post(client, alice, connectors["jira"], "ARCH-12")["status"] == "posted"
    assert {r.method for r in trackers.requests} <= {"GET", "POST", "PUT"}        # never a transition or an edit


def test_jira_rejects_names_that_are_not_issue_keys(client, alice, connectors):
    r = client.post("/knowledge/copilot/preview", headers=alice, json={"connector_id": connectors["jira"]["id"], "ref": "42"})
    assert r.status_code == 502 and "key" in r.json()["detail"]


def enable(client, headers, connector, **body) -> Dict:
    r = client.put(f"/connectors/{connector['id']}/copilot", headers=headers, json={"enabled": True, **body})
    assert r.status_code == 200, r.text
    return r.json()


def jira_event(event="jira:issue_updated") -> bytes:
    return json.dumps({"webhookEvent": event, "issue": {"key": "ARCH-12"}}).encode()


def test_jira_webhooks_signed_or_with_the_token_header(client, alice, connectors, trackers):
    connector = connectors["jira"]
    secret = enable(client, alice, connector)["webhook_secret"]
    url = f"/copilot/jira/{connector['id']}/webhook"
    body = jira_event("jira:issue_created")
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(url, content=body, headers={"X-Hub-Signature": sig}).status_code == 202
    assert len(trackers.jira_comments) == 1

    # Jira Automation can't sign: the secret in a header instead; same text again does nothing
    r = client.post(url, content=jira_event(), headers={"X-Knowledge-Hubs-Token": secret})
    assert r.status_code == 202 and len(trackers.jira_comments) == 1
    assert [x for x in trackers.requests if x.method == "PUT"] == []
    trackers.description += " Keep the old tables for a month."
    client.post(url, content=jira_event(), headers={"X-Knowledge-Hubs-Token": secret})
    assert any(x.method == "PUT" for x in trackers.requests)

    assert client.post(url, content=jira_event(), headers={"X-Knowledge-Hubs-Token": "wrong"}).status_code == 401
    assert client.post(url, content=jira_event(), headers={"X-Hub-Signature": "sha256=00"}).status_code == 401
    # a connector of another kind can't be addressed as Jira, and comment events are ignored
    assert client.post(f"/copilot/linear/{connector['id']}/webhook", content=jira_event(),
                       headers={"X-Knowledge-Hubs-Token": secret}).status_code == 401
    assert client.post(url, content=json.dumps({"webhookEvent": "comment_created"}).encode(),
                       headers={"X-Knowledge-Hubs-Token": secret}).status_code == 204


def test_done_issues_are_left_alone_by_webhooks(client, alice, connectors, trackers):
    secret = enable(client, alice, connectors["jira"])["webhook_secret"]
    trackers.done = True
    client.post(f"/copilot/jira/{connectors['jira']['id']}/webhook", content=jira_event(),
                headers={"X-Knowledge-Hubs-Token": secret})
    assert trackers.jira_comments == []
    assert client.get("/knowledge/copilot/runs", headers=alice).json() == []


# ── Linear ──────────────────────────────────────────────────────────────────

def linear_delivery(secret: str, identifier="ENG-42", action="update", kind="Issue", age_ms=0) -> Dict:
    body = json.dumps({"action": action, "type": kind, "data": {"identifier": identifier},
                       "webhookTimestamp": int(time.time() * 1000) - age_ms}).encode()
    return {"content": body, "headers": {"Linear-Signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}}


def test_linear_issue_gets_a_markdown_comment_then_an_update(client, alice, connectors, trackers):
    first = post(client, alice, connectors["linear"], "eng-42")
    assert first["status"] == "posted" and first["ref"] == "ENG-42"
    assert trackers.linear_comments[0]["body"].startswith("<!-- knowledge-hubs-copilot -->")
    assert post(client, alice, connectors["linear"], "ENG-42")["status"] == "updated"
    assert len(trackers.linear_comments) == 1
    r = client.post("/knowledge/copilot/preview", headers=alice, json={"connector_id": connectors["linear"]["id"], "ref": "ENG-9"})
    assert r.status_code == 502 and "no issue ENG-9" in r.json()["detail"]


def test_linear_uses_the_signing_secret_linear_shows(client, alice, connectors, trackers):
    connector = connectors["linear"]
    r = client.put(f"/connectors/{connector['id']}/copilot", headers=alice, json={"enabled": True})
    assert r.status_code == 422                                   # Linear creates the secret: paste it
    on = enable(client, alice, connector, webhook_secret="lin_wh_signing_secret")
    assert on["enabled"] and "webhook_secret" not in on and on["webhook_url"].endswith(f"/copilot/linear/{connector['id']}/webhook")
    url = f"/copilot/linear/{connector['id']}/webhook"
    assert client.post(url, **linear_delivery("lin_wh_signing_secret")).status_code == 202
    assert len(trackers.linear_comments) == 1
    # wrong secret, a replayed (old) delivery, and comment events
    assert client.post(url, **linear_delivery("other-secret")).status_code == 401
    assert client.post(url, **linear_delivery("lin_wh_signing_secret", age_ms=10 * 60 * 1000)).status_code == 401
    assert client.post(url, **linear_delivery("lin_wh_signing_secret", kind="Comment", action="create")).status_code == 204
    assert len(trackers.linear_comments) == 1


def test_other_connector_kinds_are_refused(client, make_user, tmp_path):
    admin = make_user(role="admin")                     # folder connectors need a server admin
    r = client.post("/connectors", headers=admin, json={"kind": "folder", "name": "notes", "config": {"path": str(tmp_path)}})
    assert r.status_code == 201, r.text
    folder = r.json()
    assert "copilot" not in folder
    assert client.get(f"/connectors/{folder['id']}/copilot", headers=admin).status_code == 400
    r = client.post("/knowledge/copilot/preview", headers=admin, json={"connector_id": folder["id"], "ref": "1"})
    assert r.status_code == 400
