"""Decision Copilot: comments from the record on GitHub pull requests, Jira and Linear issues. Comments only."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from typing import Dict, List

import httpx
import pytest

from app.services.copilot import MARKER, relevant
from tests.helpers import ingest_text

REPO = "https://github.com/acme/payments-api"
API = "https://api.github.com/repos/acme/payments-api"
MEETING = json.dumps({
    "summary": "Payments stay on PostgreSQL.",
    "decisions": [{"what": "Use PostgreSQL for the payment service", "who": "Ana",
                   "why": "payments need transactional consistency"}],
    "action_items": [], "risks": [],
})
CONFLICT = json.dumps({"assessments": [{"n": 1, "relation": "conflicts", "reason": "MongoDB drops the transactions payments rely on"}]})


class FakeGitHub:
    """Pull requests 7 (moves payments to MongoDB) and 8 (a README typo); records comments."""

    def __init__(self) -> None:
        self.comments: List[Dict] = []
        self.refuse_writes = False
        self.sha = {7: "abc123", 8: "def456"}
        self.files = {7: ["services/payments/store.py"], 8: ["README.md"]}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        prs = {7: ("Move payments to MongoDB", "Switch the payment service's store.", self.files[7]),
               8: ("Fix typo in README", "", self.files[8])}
        for n, (title, body, files) in prs.items():
            if path == f"/repos/acme/payments-api/pulls/{n}":
                return httpx.Response(200, json={"number": n, "title": title, "body": body, "state": "open",
                                                 "draft": False, "html_url": f"{REPO}/pull/{n}",
                                                 "head": {"sha": self.sha[n]}})
            if path == f"/repos/acme/payments-api/pulls/{n}/files":
                return httpx.Response(200, json=[{"filename": f} for f in files])
            if path == f"/repos/acme/payments-api/issues/{n}/comments" and method == "GET":
                return httpx.Response(200, json=[c for c in self.comments if c["pr"] == n])
            if path == f"/repos/acme/payments-api/issues/{n}/comments" and method == "POST":
                if self.refuse_writes:
                    return httpx.Response(403, json={"message": "Resource not accessible by integration"})
                c = {"id": len(self.comments) + 1, "pr": n, "body": json.loads(request.content)["body"],
                     "html_url": f"{REPO}/pull/{n}#issuecomment-{len(self.comments) + 1}"}
                self.comments.append(c)
                return httpx.Response(201, json=c)
        if path.startswith("/repos/acme/payments-api/issues/comments/") and method == "PATCH":
            c = next(c for c in self.comments if c["id"] == int(path.rsplit("/", 1)[1]))
            c["body"] = json.loads(request.content)["body"]
            return httpx.Response(200, json=c)
        return httpx.Response(404, json={"message": f"no fake for {method} {path}"})


@pytest.fixture
def github(mock_http):
    fake = FakeGitHub()
    fake.requests = mock_http(fake)
    return fake


@pytest.fixture
def setup(client, alice, llm, github):
    """A GitHub connector, the Payments system, and a decision about it."""
    r = client.post("/connectors", headers=alice, json={"kind": "github", "name": "payments-api",
                                                       "config": {"repo_url": REPO, "token": "ghp_secret123"}})
    assert r.status_code == 201, r.text
    connector = r.json()
    client.post("/knowledge/systems", headers=alice, json={"name": "Payments", "aliases": ["payment service"]})
    llm.handlers["transcript"] = lambda m: MEETING
    client.post("/knowledge/artifacts/transcript", headers=alice, json={
        "title": "Architecture review", "content": "Ana: payments stay on PostgreSQL.", "source_type": "transcript"})
    github.requests.clear()
    return connector


def preview(client, headers, connector, n=7) -> Dict:
    r = client.post("/knowledge/copilot/preview", headers=headers, json={"connector_id": connector["id"], "ref": str(n)})
    assert r.status_code == 200, r.text
    return r.json()


def signed(secret: str, payload: Dict, event="pull_request") -> Dict:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"content": body, "headers": {"X-GitHub-Event": event, "X-Hub-Signature-256": sig,
                                         "Content-Type": "application/json"}}


def pr_event(n=7, action="opened", sha=None, draft=False) -> Dict:
    return {"action": action, "pull_request": {"number": n, "state": "open", "draft": draft,
                                               "head": {"sha": sha or {7: "abc123", 8: "def456"}[n]}}}


# ── on demand ───────────────────────────────────────────────────────────────

def test_preview_shows_the_comment_without_posting(client, alice, setup, github, llm):
    llm.handlers["impact"] = lambda m: CONFLICT
    body = preview(client, alice, setup)
    assert body["target"] == {"ref": "7", "title": "Move payments to MongoDB", "url": f"{REPO}/pull/7",
                              "noun": "pull request", "skip": None}
    assert body["relevant"] is True and body["files"] == 1
    comment = body["comment"]
    assert comment.startswith(MARKER) and "may conflict with 1 recorded decision" in comment
    assert "Use PostgreSQL for the payment service" in comment and "MongoDB drops the transactions" in comment
    assert "Why it was decided: payments need transactional consistency" in comment
    assert "**Systems:** Payments" in comment and "Advisory only" in comment
    # the changed file paths are part of what's analysed; nothing was written to GitHub
    assert "services/payments/store.py" in llm.last("impact")[-1]["content"]
    assert {r.method for r in github.requests} == {"GET"}
    assert all(r.headers["authorization"] == "Bearer ghp_secret123" for r in github.requests)


def test_posting_comments_once_then_updates_it(client, alice, setup, github, llm):
    llm.handlers["impact"] = lambda m: CONFLICT
    ref = {"connector_id": setup["id"], "ref": "7"}
    first = client.post("/knowledge/copilot/post", headers=alice, json=ref).json()
    assert first["status"] == "posted" and first["comment_url"].endswith("#issuecomment-1")
    second = client.post("/knowledge/copilot/post", headers=alice, json=ref).json()
    assert second["status"] == "updated" and len(github.comments) == 1
    assert {r.method for r in github.requests} == {"GET", "POST", "PATCH"}       # never a review, label or merge
    runs = client.get("/knowledge/copilot/runs", headers=alice).json()
    assert [r["status"] for r in runs] == ["updated", "posted"] and runs[0]["counts"]["conflicts"] == 1


def test_a_change_the_record_says_nothing_about_gets_no_comment(client, alice, setup, github):
    body = preview(client, alice, setup, n=8)
    assert body["relevant"] is False
    assert relevant({"decisions": [{"relation": "unrelated", "reasons": ["mentions Payments"]}],
                     "risks": [], "lessons": []}) is False


def test_github_refusing_the_comment_is_reported(client, alice, setup, github):
    github.refuse_writes = True
    r = client.post("/knowledge/copilot/post", headers=alice, json={"connector_id": setup["id"], "ref": "7"})
    assert r.status_code == 502 and "403" in r.json()["detail"]
    assert client.get("/knowledge/copilot/runs", headers=alice).json()[0]["status"] == "failed"


def test_other_spaces_cannot_use_the_connector(client, alice, bob, setup):
    r = client.post("/knowledge/copilot/preview", headers=bob, json={"connector_id": setup["id"], "ref": "7"})
    assert r.status_code == 404
    assert client.get(f"/connectors/{setup['id']}/copilot", headers=bob).status_code == 404


# ── the webhook ─────────────────────────────────────────────────────────────

def enable(client, headers, connector) -> Dict:
    r = client.put(f"/connectors/{connector['id']}/copilot", headers=headers, json={"enabled": True})
    assert r.status_code == 200, r.text
    return r.json()


def test_enabling_creates_a_secret_shown_once(client, alice, setup):
    on = enable(client, alice, setup)
    assert on["enabled"] and len(on["webhook_secret"]) >= 40
    assert on["webhook_url"].endswith(f"/copilot/github/{setup['id']}/webhook")
    again = client.get(f"/connectors/{setup['id']}/copilot", headers=alice).json()
    assert again == {"enabled": True, "has_secret": True, "webhook_url": on["webhook_url"]}
    assert enable(client, alice, setup).get("webhook_secret") is None                # not shown again
    rotated = client.put(f"/connectors/{setup['id']}/copilot", headers=alice,
                         json={"enabled": True, "rotate_secret": True}).json()
    assert rotated["webhook_secret"] != on["webhook_secret"]
    # the connector's own view shows the state, never the secret or its ciphertext
    listed = next(c for c in client.get("/connectors", headers=alice).json() if c["id"] == setup["id"])
    assert listed["copilot"] == {"enabled": True, "has_secret": True}
    assert rotated["webhook_secret"] not in json.dumps(listed) and "webhook_secret" not in json.dumps(listed)


def test_a_signed_pull_request_event_gets_a_comment(client, alice, setup, github, llm):
    llm.handlers["impact"] = lambda m: CONFLICT
    secret = enable(client, alice, setup)["webhook_secret"]
    url = f"/copilot/github/{setup['id']}/webhook"
    assert client.post(url, **signed(secret, {"zen": "hi"}, event="ping")).status_code == 204
    r = client.post(url, **signed(secret, pr_event()))
    assert r.status_code == 202
    assert len(github.comments) == 1 and github.comments[0]["body"].startswith(MARKER)
    assert client.get("/knowledge/copilot/runs", headers=alice).json()[0]["source"] == "webhook"
    # nothing it analyses changed (a push to the same files): no second comment or edit
    github.sha[7] = "fff999"
    client.post(url, **signed(secret, pr_event(action="synchronize", sha="fff999")))
    assert [r.method for r in github.requests if r.method != "GET"] == ["POST"]
    # it touches another file now: analysed again, and the one comment is updated
    github.files[7] = ["services/payments/store.py", "services/payments/migrate.py"]
    client.post(url, **signed(secret, pr_event(action="synchronize", sha="fff999")))
    assert len(github.comments) == 1 and any(r.method == "PATCH" for r in github.requests)


def test_quiet_when_nothing_is_relevant_or_it_is_a_draft(client, alice, setup, github):
    secret = enable(client, alice, setup)["webhook_secret"]
    url = f"/copilot/github/{setup['id']}/webhook"
    assert client.post(url, **signed(secret, pr_event(n=8))).status_code == 202
    assert github.comments == []
    assert client.get("/knowledge/copilot/runs", headers=alice).json()[0]["status"] == "skipped"
    assert client.post(url, **signed(secret, pr_event(draft=True))).status_code == 204
    assert client.post(url, **signed(secret, pr_event(action="closed"))).status_code == 204
    assert client.post(url, **signed(secret, {"action": "created"}, event="issue_comment")).status_code == 204


def test_unsigned_wrongly_signed_or_disabled_webhooks_are_refused(client, alice, setup, github):
    url = f"/copilot/github/{setup['id']}/webhook"
    assert client.post(url, **signed("anything", pr_event())).status_code == 401          # never enabled: no secret
    secret = enable(client, alice, setup)["webhook_secret"]
    assert client.post(url, **signed("wrong-secret", pr_event())).status_code == 401
    assert client.post(url, content=b"{}", headers={"X-GitHub-Event": "pull_request"}).status_code == 401
    assert client.post("/copilot/github/conn_nope/webhook", **signed(secret, pr_event())).status_code == 401
    client.put(f"/connectors/{setup['id']}/copilot", headers=alice, json={"enabled": False})
    assert client.post(url, **signed(secret, pr_event())).status_code == 204
    assert github.comments == []


def test_deleting_the_connector_deletes_its_runs(client, alice, setup, github, db_query):
    client.post("/knowledge/copilot/post", headers=alice, json={"connector_id": setup["id"], "ref": "8"})
    assert db_query("SELECT count(*) AS n FROM CopilotRun WHERE connector_id = :c", c=setup["id"])[0]["n"] == 1
    assert client.delete(f"/connectors/{setup['id']}", headers=alice).status_code == 204
    assert db_query("SELECT count(*) AS n FROM CopilotRun WHERE connector_id = :c", c=setup["id"])[0]["n"] == 0


# ── where the analysis runs ─────────────────────────────────────────────────

REDIS = os.getenv("KH_TEST_REDIS_URL")


def test_a_pull_request_already_being_analysed_is_not_analysed_again(client, alice, setup, github, llm):
    """GitHub often sends two events at once (opened, then edited): only one analysis runs."""
    from app import coordination
    llm.handlers["impact"] = lambda m: CONFLICT
    secret = enable(client, alice, setup)["webhook_secret"]
    from app.services.copilot import copilot_lock
    lock = copilot_lock(setup["id"], "7")
    token = asyncio.run(coordination.acquire(lock, ttl=30))
    try:
        assert client.post(f"/copilot/github/{setup['id']}/webhook", **signed(secret, pr_event())).status_code == 202
        assert github.comments == []
    finally:
        asyncio.run(coordination.release(lock, token))
    client.post(f"/copilot/github/{setup['id']}/webhook", **signed(secret, pr_event()))
    assert len(github.comments) == 1


@pytest.mark.skipif(not REDIS, reason="set KH_TEST_REDIS_URL to run Redis-backed tests")
def test_in_worker_mode_the_webhook_only_queues_the_analysis(client, alice, setup, github, monkeypatch):
    from app import worker
    secret = enable(client, alice, setup)["webhook_secret"]
    sent = []
    monkeypatch.setenv("REDIS_URL", REDIS)
    monkeypatch.setattr(worker.celery_app, "send_task", lambda name, args: sent.append((name, args)))
    assert client.post(f"/copilot/github/{setup['id']}/webhook", **signed(secret, pr_event())).status_code == 202
    space = client.get("/auth/me", headers=alice).json()["id"]
    assert sent == [("kh.copilot", [space, setup["id"], "7"])]
    assert github.comments == [] and github.requests == []                  # nothing ran in the API process

    # what the worker then executes, with its own database client
    worker.copilot_job.run(space, setup["id"], "7")
    runs = client.get("/knowledge/copilot/runs", headers=alice).json()
    assert runs[0]["source"] == "webhook" and runs[0]["ref"] == "7" and runs[0]["kind"] == "github"
