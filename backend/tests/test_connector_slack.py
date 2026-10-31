"""The Slack connector: threads and each day's loose messages become conversations to extract from."""
from __future__ import annotations

import time
from typing import Dict

import httpx

from tests.test_connectors import artifacts, sync

NOW = time.time()
TS = lambda hours_ago: f"{NOW - hours_ago * 3600:.6f}"   # noqa: E731
THREAD = TS(30)


def slack_api(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == "Bearer xoxb-test"
    method, p = request.url.path.rsplit("/", 1)[1], dict(request.url.params)
    if p.get("channel") == "C_PRIVATE":
        return httpx.Response(200, json={"ok": False, "error": "not_in_channel"})
    if method == "conversations.info":
        return httpx.Response(200, json={"ok": True, "channel": {"name": "platform"}})
    if method == "conversations.history":
        return httpx.Response(200, json={"ok": True, "has_more": False, "messages": [
            {"ts": THREAD, "user": "U1", "user_profile": {"real_name": "Lena"}, "reply_count": 2,
             "text": "Should we keep Kafka for domain events?"},
            {"ts": TS(5), "user": "U2", "user_profile": {"real_name": "Omar"},
             "text": "Heads up: we agreed to freeze deploys on Friday afternoons from now on."},
            {"ts": TS(4), "subtype": "channel_join", "text": "Ana joined"},
        ]})
    if method == "conversations.replies":
        assert p["ts"] == THREAD
        return httpx.Response(200, json={"ok": True, "messages": [
            {"ts": THREAD, "user_profile": {"real_name": "Lena"}, "text": "Should we keep Kafka for domain events?"},
            {"ts": TS(29), "user_profile": {"real_name": "Omar"},
             "text": "We decided to replace Kafka with NATS JetStream because the cluster costs too much."},
            {"ts": TS(28), "user_profile": {"real_name": "Lena"}, "text": "Action: Lena will write the migration plan."},
        ]})
    return httpx.Response(404, json={"ok": False, "error": "unknown_method"})


def create(client, headers, channels) -> Dict:
    r = client.post("/connectors", headers=headers, json={"kind": "slack", "name": "Slack", "config": {
        "api_url": "https://slack.com/api", "bot_token": "xoxb-test", "channels": channels}})
    assert r.status_code == 201, r.text
    return r.json()


def test_threads_and_days_become_conversations(client, alice, mock_http):
    seen = mock_http(slack_api)
    connector = create(client, alice, "C_PLATFORM")
    assert connector["config"]["bot_token"] == "********"
    sync(client, alice, connector["id"])
    slack_sources = [a for a in artifacts(client, alice)["artifacts"] if a["source_type"] == "slack"]
    titles = {a["title"] for a in slack_sources}
    assert "#platform: Should we keep Kafka for domain events?" in titles
    assert any(t.startswith("#platform on ") for t in titles)
    thread = next(a for a in slack_sources if a["title"].startswith("#platform: "))
    assert "Omar: We decided to replace Kafka with NATS JetStream" in thread["content"]
    assert thread["source"].startswith("https://slack.com/archives/C_PLATFORM/p")
    items = client.get("/knowledge", headers=alice).json()["knowledge_items"]
    assert any(i["type"] == "decision" and "NATS" in i["title"] for i in items)
    assert {r.url.host for r in seen} == {"slack.com"}                 # nothing else contacted
    assert not any("join" in a["content"] for a in slack_sources)


def test_a_channel_it_cannot_read_is_reported_and_the_rest_synced(client, alice, mock_http):
    mock_http(slack_api)
    connector = create(client, alice, "C_PRIVATE, C_PLATFORM")
    after = sync(client, alice, connector["id"])
    assert after["last_status"] == "partial"
    assert "not_in_channel" in str(after["last_result"]) and "invite the app" in str(after["last_result"])
