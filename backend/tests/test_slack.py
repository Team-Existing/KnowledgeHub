"""Slack's /decisions command: request signing, quick acknowledgement, the answer through response_url."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Dict
from urllib.parse import urlencode

import httpx
import pytest

from app.services.slack import parse, response_url_ok

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
RESPONSE_URL = "https://hooks.slack.com/commands/T123/456/abc"
MEETING = json.dumps({
    "summary": "Payments stay on PostgreSQL.",
    "decisions": [{"what": "Use PostgreSQL for the payment service", "who": "Ana",
                   "why": "payments need transactional consistency"}],
    "action_items": [], "risks": [],
})
CONFLICT = json.dumps({"assessments": [{"n": 1, "relation": "conflicts", "reason": "MongoDB drops the transactions payments rely on"}]})


@pytest.fixture
def slack_hooks(mock_http):
    """Slack's response_url endpoint: records what was posted to it."""
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append({"url": str(request.url), **json.loads(request.content)})
        return httpx.Response(200, text="ok")
    seen = mock_http(handler)
    return posted, seen


@pytest.fixture
def app_in_slack(client, alice, llm, slack_hooks) -> Dict:
    """A Slack app for alice's space, with the Payments system and a decision about it."""
    r = client.post("/knowledge/slack", headers=alice, json={"name": "Acme workspace", "signing_secret": SECRET})
    assert r.status_code == 201, r.text
    client.post("/knowledge/systems", headers=alice, json={"name": "Payments", "aliases": ["payment service"]})
    llm.handlers["transcript"] = lambda m: MEETING
    client.post("/knowledge/artifacts/transcript", headers=alice, json={
        "title": "Architecture review", "content": "Ana: payments stay on PostgreSQL.", "source_type": "transcript"})
    llm.handlers["impact"] = lambda m: CONFLICT
    return r.json()


def slash(client, integration, text, secret=SECRET, age=0, response_url=RESPONSE_URL, user="omar"):
    body = urlencode({"command": "/decisions", "text": text, "user_name": user, "team_id": "T123",
                      "response_url": response_url}).encode()
    ts = str(int(time.time()) - age)
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return client.post(f"/slack/{integration['id']}/command", content=body, headers={
        "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded"})


# ── pure ────────────────────────────────────────────────────────────────────

def test_share_prefix_and_reply_addresses():
    assert parse("move payments to MongoDB") == ("move payments to MongoDB", False)
    assert parse("share  move payments to MongoDB") == ("move payments to MongoDB", True)
    assert parse("Share") == ("", True)
    assert response_url_ok("https://hooks.slack.com/commands/T1/2/x")
    for bad in ("http://hooks.slack.com/x", "https://hooks.slack.com.evil.test/x", "https://evil.test/x", "nonsense"):
        assert not response_url_ok(bad), bad


# ── managing the app ────────────────────────────────────────────────────────

def test_adding_a_slack_app_shows_where_to_point_the_command(client, alice, bob, app_in_slack, db_query):
    assert app_in_slack["request_url"].endswith(f"/slack/{app_in_slack['id']}/command")
    assert app_in_slack["command"] == "/decisions"
    listed = client.get("/knowledge/slack", headers=alice).json()
    assert [i["id"] for i in listed] == [app_in_slack["id"]] and "signing_secret" not in json.dumps(listed)
    stored = db_query("SELECT signing_secret FROM SlackIntegration WHERE id = :id", id=app_in_slack["id"])[0]
    assert SECRET not in stored["signing_secret"]                      # encrypted at rest
    assert client.get("/knowledge/slack", headers=bob).json() == []
    assert client.delete(f"/knowledge/slack/{app_in_slack['id']}", headers=bob).status_code == 404
    assert client.post("/knowledge/slack", headers=alice, json={"name": "x", "signing_secret": "short"}).status_code == 422


# ── the command ─────────────────────────────────────────────────────────────

def test_the_command_answers_privately_with_what_the_record_says(client, app_in_slack, slack_hooks):
    posted, seen = slack_hooks
    r = slash(client, app_in_slack, "move the payment service to MongoDB")
    assert r.status_code == 200
    assert r.json() == {"response_type": "ephemeral",
                        "text": "Looking up what the record says about: _move the payment service to MongoDB_ …"}
    assert len(posted) == 1 and posted[0]["url"] == RESPONSE_URL
    answer = posted[0]
    assert answer["response_type"] == "ephemeral" and answer["replace_original"] is True
    text = answer["text"]
    assert text.startswith("*What the record says about: move the payment service to MongoDB*")
    assert ":warning: This change may conflict with 1 recorded decision" in text
    assert "• *Use PostgreSQL for the payment service*" in text and "MongoDB drops the transactions" in text
    assert "*Systems:* Payments" in text and "Asked by omar" in text
    assert answer["blocks"][0]["text"]["type"] == "mrkdwn"
    assert {req.url.host for req in seen} == {"hooks.slack.com"}       # nothing else contacted


def test_share_posts_in_the_channel(client, app_in_slack, slack_hooks):
    posted, _ = slack_hooks
    slash(client, app_in_slack, "share move the payment service to MongoDB")
    assert posted[0]["response_type"] == "in_channel"


def test_nothing_on_record_says_so(client, app_in_slack, slack_hooks, llm):
    posted, _ = slack_hooks
    llm.handlers.pop("impact")
    slash(client, app_in_slack, "repaint the office kitchen")
    assert posted[0]["text"].startswith("Nothing on record bears on _repaint the office kitchen_")


def test_help_and_empty_text_answer_at_once(client, app_in_slack, slack_hooks):
    posted, _ = slack_hooks
    for text in ("", "help", "share"):
        r = slash(client, app_in_slack, text)
        assert r.status_code == 200 and "/decisions move the payment service" in r.json()["text"]
    assert posted == []


def test_requests_that_are_not_from_slack_are_refused(client, app_in_slack, slack_hooks):
    posted, _ = slack_hooks
    assert slash(client, app_in_slack, "x", secret="another-secret-entirely-1234").status_code == 401
    assert slash(client, app_in_slack, "x", age=10 * 60).status_code == 401              # a replay
    assert client.post(f"/slack/{app_in_slack['id']}/command", content=b"text=x").status_code == 401
    assert slash(client, {"id": "slk_nope"}, "x").status_code == 401
    # signed, but the reply would go somewhere other than Slack: nothing is sent
    r = slash(client, app_in_slack, "move payments", response_url="https://evil.test/collect")
    assert r.status_code == 200 and "reply address" in r.json()["text"]
    assert posted == []


def test_a_replaced_secret_takes_over_and_a_removed_app_stops(client, alice, app_in_slack, slack_hooks):
    new = "a-brand-new-signing-secret-0000"
    assert client.put(f"/knowledge/slack/{app_in_slack['id']}", headers=alice, json={"signing_secret": new}).status_code == 204
    assert slash(client, app_in_slack, "help").status_code == 401
    assert slash(client, app_in_slack, "help", secret=new).status_code == 200
    assert client.delete(f"/knowledge/slack/{app_in_slack['id']}", headers=alice).status_code == 204
    assert slash(client, app_in_slack, "help", secret=new).status_code == 401


REDIS = os.getenv("KH_TEST_REDIS_URL")


@pytest.mark.skipif(not REDIS, reason="set KH_TEST_REDIS_URL to run Redis-backed tests")
def test_in_worker_mode_the_answer_comes_from_the_worker(client, alice, app_in_slack, slack_hooks, monkeypatch):
    from app import worker
    posted, _ = slack_hooks
    sent = []
    monkeypatch.setenv("REDIS_URL", REDIS)
    monkeypatch.setattr(worker.celery_app, "send_task", lambda name, args: sent.append((name, args)))
    assert slash(client, app_in_slack, "move the payment service to MongoDB").status_code == 200
    space = client.get("/auth/me", headers=alice).json()["id"]
    assert sent == [("kh.slack_command", [space, app_in_slack["id"], "move the payment service to MongoDB",
                                          RESPONSE_URL, "omar"])]
    assert posted == []
    worker.slack_command.run(*sent[0][1])
    assert len(posted) == 1 and "Payments" in posted[0]["text"]
