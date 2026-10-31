"""The weekly digest: what needs attention, posted to a Slack incoming webhook on the chosen day."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.services import digest
from tests.helpers import ingest_text

HOOK = "https://hooks.slack.com/services/T000/B000/XXXX"


@pytest.fixture
def slack(mock_http):
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append({"url": str(request.url), **json.loads(request.content)})
        return httpx.Response(200, text="ok")
    mock_http(handler)
    return posted


@pytest.fixture
def busy_space(client, alice):
    """A flagged decision, a review due, an overdue commitment."""
    items = ingest_text(client, alice, title="Notes", content=(
        "We decided to rotate API signing keys every 90 days.\n"
        "We decided to use PostgreSQL for the payment service."))["items"]
    keys, pg = (next(i for i in items if word in i["title"]) for word in ("rotate", "PostgreSQL"))
    client.post(f"/knowledge/items/{keys['id']}/health/flag", headers=alice, json={"note": "the vault rotates keys now"})
    client.put(f"/knowledge/items/{pg['id']}/review-date", headers=alice, json={"review_due": date.today().isoformat()})
    return {"keys": keys, "pg": pg}


def add(client, headers, **body):
    r = client.post("/knowledge/digests", headers=headers, json={"name": "#platform", "webhook_url": HOOK, **body})
    assert r.status_code == 201, r.text
    return r.json()


def test_preview_lists_what_needs_attention(client, alice, busy_space):
    body = client.get("/knowledge/digests/preview", headers=alice).json()
    titles = {s["title"]: s for s in body["sections"]}
    assert set(titles) == {"May be out of date", "Due for review"} and body["total"] == 2
    stale = titles["May be out of date"]["entries"][0]
    assert stale["id"] == busy_space["keys"]["id"] and "the vault rotates keys now" in stale["note"]


def test_send_now_posts_to_the_webhook_and_records_it(client, alice, busy_space, slack, db_query):
    d = add(client, alice, weekday=2)
    assert "webhook_url" not in d
    stored = db_query("SELECT webhook_url FROM Digest WHERE id = :id", id=d["id"])[0]["webhook_url"]
    assert "XXXX" not in stored                                                  # encrypted at rest
    r = client.post(f"/knowledge/digests/{d['id']}/send", headers=alice).json()
    assert r == {"status": "sent", "total": 2}
    assert slack[0]["url"] == HOOK and slack[0]["text"].startswith("*Knowledge Hubs: what needs attention")
    assert "*May be out of date* (1)" in slack[0]["text"] and "rotate API signing keys" in slack[0]["text"]
    listed = client.get("/knowledge/digests", headers=alice).json()["digests"][0]
    assert listed["last_status"] == "sent" and "webhook_url" not in listed


def test_only_slack_webhooks_are_accepted(client, alice):
    for bad in ("https://evil.test/services/x/y/z", "http://hooks.slack.com/services/a/b/c",
                "https://hooks.slack.com/commands/a/b/c"):
        r = client.post("/knowledge/digests", headers=alice, json={"name": "x", "webhook_url": bad})
        assert r.status_code == 422, bad


def test_the_schedule_sends_on_the_day_after_the_hour_once_and_skips_empty(client, alice, bob, busy_space, slack):
    from app.db import get_client
    now = datetime.now(timezone.utc).replace(hour=digest.digest_hour() + 1 if digest.digest_hour() < 23 else 23)
    due = add(client, alice, weekday=now.weekday())
    add(client, alice, weekday=(now.weekday() + 1) % 7)                          # another day
    quiet = add(client, bob, weekday=now.weekday())                              # bob's space has nothing

    async def run(at):
        return await digest.send_due(get_client(), now=at)
    portal = client.portal if hasattr(client, "portal") else None
    sent = (portal.call(run, now) if portal else asyncio.run(run(now)))
    by_id = {s["id"]: s["status"] for s in sent}
    assert by_id.get(due["id"]) == "sent" and by_id.get(quiet["id"]) == "nothing to report"
    assert len([p for p in slack if "what needs attention" in p["text"]]) == 1
    again = portal.call(run, now) if portal else asyncio.run(run(now))
    assert due["id"] not in {s["id"] for s in again}                             # once a day
    early = now.replace(hour=max(0, digest.digest_hour() - 1)) if digest.digest_hour() > 0 else None
    if early:
        assert (portal.call(run, early) if portal else asyncio.run(run(early))) == []


def test_other_spaces_cannot_touch_a_digest(client, alice, bob):
    d = add(client, alice)
    assert client.post(f"/knowledge/digests/{d['id']}/send", headers=bob).status_code == 404
    assert client.delete(f"/knowledge/digests/{d['id']}", headers=bob).status_code == 404
    assert client.delete(f"/knowledge/digests/{d['id']}", headers=alice).status_code == 204
