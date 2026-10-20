"""
The pieces that let the app run as several instances with a background worker
and an ArcadeDB cluster: the multi-node database client, shared locks,
dispatching syncs to the worker, the worker's tasks, and /health.

Redis-backed tests need a Redis server: set KH_TEST_REDIS_URL (e.g.
redis://localhost:6390/15). They are skipped otherwise.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import List

import httpx
import pytest

from app import coordination
from app.arcadedb import ArcadeClient, ArcadeDBError

REDIS = os.getenv("KH_TEST_REDIS_URL")
needs_redis = pytest.mark.skipif(not REDIS, reason="set KH_TEST_REDIS_URL to run Redis-backed tests")


# ── multi-node database client (no database: mocked nodes) ──────────────────

def _client(handler) -> tuple[ArcadeClient, List[str]]:
    seen: List[str] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return handler(request)

    client = ArcadeClient("http://node-a:2480,http://node-b:2480", "db", "u", "p")
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    client._leader_checked = float("inf")   # these tests are about failover; leader lookup has its own
    return client, seen


def _ok(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/begin/db"):
        return httpx.Response(204, headers={"arcadedb-session-id": "tx-1"})
    return httpx.Response(200, json={"result": [{"n": 1}]})


def test_a_down_node_is_skipped_and_remembered():
    def handler(request):
        if request.url.host == "node-a":
            raise httpx.ConnectError("refused", request=request)
        return _ok(request)
    client, seen = _client(handler)
    assert asyncio.run(client.command("SELECT 1")) == [{"n": 1}]
    asyncio.run(client.command("SELECT 1"))
    assert seen == ["node-a", "node-b", "node-b"]          # second call goes straight to the working node


def test_not_the_leader_is_retried_on_another_node():
    def handler(request):
        if request.url.host == "node-a":
            return httpx.Response(503, json={"exception": "com.arcadedb.network.binary.ServerIsNotTheLeaderException",
                                             "detail": "Leader address: node-b"})
        return _ok(request)
    client, seen = _client(handler)
    assert asyncio.run(client.server_command("create database db")) == [{"n": 1}]
    assert seen == ["node-a", "node-b"]


def test_timeouts_are_not_retried_elsewhere():
    """The statement may have run; repeating it on another node could apply a write twice."""
    def handler(request):
        if request.url.host == "node-a":
            raise httpx.ReadTimeout("slow", request=request)
        return _ok(request)
    client, seen = _client(handler)
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(client.command("INSERT INTO T SET x = 1"))
    assert seen == ["node-a"]


def test_a_transaction_stays_on_its_node():
    down = {"node-a"}

    def handler(request):
        if request.url.host in down:
            raise httpx.ConnectError("refused", request=request)
        return _ok(request)
    client, seen = _client(handler)

    async def tx():
        sid = await client.begin()                          # node-a down: begins on node-b
        down.clear()                                        # node-a is back, and preferred order would pick it...
        client._preferred = 0
        await client.command("INSERT INTO T SET x = 1", session_id=sid)
        await client.commit(sid)
        return sid
    asyncio.run(tx())
    assert seen == ["node-a", "node-b", "node-b", "node-b"]  # ...but the transaction never leaves node-b
    assert client._session_nodes == {}


def test_a_node_lost_mid_transaction_is_an_error_not_a_silent_retry():
    def handler(request):
        if request.headers.get("arcadedb-session-id"):
            raise httpx.ConnectError("gone", request=request)
        return _ok(request)
    client, _ = _client(handler)

    async def tx():
        sid = await client.begin()
        await client.command("INSERT INTO T SET x = 1", session_id=sid)
    with pytest.raises(ArcadeDBError, match="mid-transaction"):
        asyncio.run(tx())


def test_app_user_without_a_database_is_told_to_provision():
    def handler(request):
        if "/exists/" in request.url.path:
            return httpx.Response(200, json={"result": False})
        return httpx.Response(403, json={"exception": "ServerSecurityException",
                                         "detail": "Only root user is authorized to execute server commands"})
    client, _ = _client(handler)
    with pytest.raises(ArcadeDBError, match="provisioning"):
        asyncio.run(client.ensure_database())


# ── locks ───────────────────────────────────────────────────────────────────

def _lock_semantics() -> None:
    async def go():
        name = f"test:{os.urandom(4).hex()}"
        first = await coordination.acquire(name, ttl=30)
        assert first and await coordination.acquire(name, ttl=30) is None    # exclusive
        assert await coordination.is_held(name)
        await coordination.release(name, "not-the-token")                     # only the holder can release
        assert await coordination.is_held(name)
        await coordination.release(name, first)
        assert not await coordination.is_held(name)
        async with coordination.held_lock(name, ttl=30) as token:
            assert token and await coordination.is_held(name)
            async with coordination.held_lock(name, ttl=30) as second:
                assert second is None                                         # nested holder is refused
        assert not await coordination.is_held(name)
    asyncio.run(go())


def test_in_process_locks(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    _lock_semantics()


@needs_redis
def test_redis_locks_are_shared_and_expire(monkeypatch):
    monkeypatch.setenv("REDIS_URL", REDIS)
    _lock_semantics()

    async def take(ttl):
        return await coordination.acquire("test:expiry", ttl=ttl)
    # each asyncio.run is a fresh event loop, like separate worker tasks / processes
    assert asyncio.run(take(1)) and asyncio.run(take(1)) is None
    import time
    time.sleep(1.5)
    assert asyncio.run(take(1)) is not None                                   # a dead holder's lock lapses


# ── dispatch and the worker ─────────────────────────────────────────────────

def _folder_connector(client, admin, tmp_path: Path) -> dict:
    (tmp_path / "notes.md").write_text("We decided to run two app instances behind Caddy.", encoding="utf-8")
    r = client.post("/connectors", headers=admin, json={"kind": "folder", "name": "f", "config": {"path": str(tmp_path)}})
    assert r.status_code == 201, r.text
    return r.json()


def _status(client, headers, connector_id) -> dict:
    return next(c for c in client.get("/connectors", headers=headers).json() if c["id"] == connector_id)


@needs_redis
def test_in_worker_mode_the_api_only_queues_the_sync(client, make_user, tmp_path, monkeypatch):
    admin = make_user(role="admin")
    conn = _folder_connector(client, admin, tmp_path)
    sent = []
    from app import worker
    monkeypatch.setenv("REDIS_URL", REDIS)
    monkeypatch.setattr(worker.celery_app, "send_task", lambda name, args: sent.append((name, args)))
    r = client.post(f"/connectors/{conn['id']}/sync", headers=admin)
    assert r.status_code == 202 and r.json()["last_status"] == "queued"
    me = client.get("/auth/me", headers=admin).json()
    assert sent == [("kh.sync_connector", [me["id"], conn["id"], me["id"]])]
    assert _status(client, admin, conn["id"])["last_status"] == "queued"     # nothing ran in the API process


@needs_redis
def test_the_worker_task_runs_the_sync_with_its_own_client(client, make_user, tmp_path, monkeypatch):
    admin = make_user(role="admin")
    conn = _folder_connector(client, admin, tmp_path)
    me = client.get("/auth/me", headers=admin).json()
    monkeypatch.setenv("REDIS_URL", REDIS)
    from app import worker
    worker.sync_connector.run(me["id"], conn["id"], me["id"])                 # what a Celery worker executes
    status = _status(client, admin, conn["id"])
    assert status["last_status"] == "ok" and status["last_result"]["created"] == 1

    # a second copy of the task (redelivery, double click) while one holds the lock does nothing
    async def hold():
        return await coordination.acquire(coordination.sync_lock(conn["id"]), ttl=30)
    token = asyncio.run(hold())
    (tmp_path / "more.md").write_text("We decided something new.", encoding="utf-8")
    worker.sync_connector.run(me["id"], conn["id"], me["id"])
    assert _status(client, admin, conn["id"])["last_result"]["created"] == 1   # skipped: lock held elsewhere
    assert _status(client, admin, conn["id"])["last_status"] == "running"      # the lock holder is "running"
    asyncio.run(coordination.release(coordination.sync_lock(conn["id"]), token))


@needs_redis
def test_the_worker_retention_task_runs_once_at_a_time(client, monkeypatch):
    monkeypatch.setenv("REDIS_URL", REDIS)
    monkeypatch.setenv("RETENTION_ENABLED", "true")
    from app import worker

    async def hold():
        return await coordination.acquire("retention", ttl=30)
    token = asyncio.run(hold())
    report = worker.run_retention.run()
    assert report["skipped"] == [{"reason": "another clean-up run is in progress"}]
    asyncio.run(coordination.release("retention", token))
    assert "users_deleted" in worker.run_retention.run()


def test_health_reports_nodes_and_background_mode(client):
    body = client.get("/health").json()
    assert body["arcadedb"] == "connected" and body["arcadedb_nodes"]["ready"] >= 1
    assert body["background"] in ("worker", "in-process")


def test_health_probes_are_parallel_and_short_so_a_dead_node_cannot_fail_the_load_balancer():
    """
    Regression: probing nodes one by one with the full timeout made /health take
    15 s with one node down, longer than the load balancer's check, which then
    took every healthy app instance out of rotation. Probes now run in parallel,
    each with a short timeout of its own.
    """
    import time
    timeouts = []

    async def slow(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["read"])
        await asyncio.sleep(0.5)
        return httpx.Response(204 if request.url.host != "node-a" else 503)

    client = ArcadeClient("http://node-a:2480,http://node-b:2480,http://node-c:2480", "db", "u", "p")
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    started = time.monotonic()
    status = asyncio.run(client.node_status())
    assert time.monotonic() - started < 1.2            # three 0.5 s probes at once, not 1.5 s in a row
    assert [s["ready"] for s in status] == [False, True, True]
    assert timeouts == [ArcadeClient.PROBE_TIMEOUT] * 3 and ArcadeClient.PROBE_TIMEOUT <= 3


def test_connecting_to_a_dead_node_is_bounded():
    client = ArcadeClient("http://node-a:2480", "db", "u", "p", timeout=60)
    assert client.http.timeout.connect == ArcadeClient.CONNECT_TIMEOUT <= 5
    assert client.http.timeout.read == 60          # long queries are still allowed


def test_an_unreachable_redis_fails_fast(monkeypatch):
    """Regression: a hanging Redis ping made /health slow enough for the load balancer to drop every instance."""
    import time
    monkeypatch.setenv("REDIS_URL", "redis://10.255.255.1:6379/0")    # unroutable: connect would hang
    coordination._redis_clients.clear()
    started = time.monotonic()
    assert asyncio.run(coordination.ping()) is False
    assert time.monotonic() - started < coordination.REDIS_TIMEOUT + 1.5


def test_a_failed_redis_is_left_alone_for_a_while(monkeypatch):
    """
    Regression: with the Redis container stopped, every health probe started another
    lookup of its (now unknown) host name; they piled up in the resolver threads until
    the sign-in's Ollama check timed out and sign-in answered 503 "Ollama is not running".
    """
    monkeypatch.setenv("REDIS_URL", "redis://10.255.255.2:6379/0")
    coordination._redis_clients.clear()
    coordination.reset_redis_backoff()
    dials = []

    class Hanging:
        async def ping(self):
            dials.append(1)
            await asyncio.sleep(60)
    monkeypatch.setattr(coordination, "_redis", lambda: Hanging())
    monkeypatch.setattr(coordination, "REDIS_TIMEOUT", 0.2)
    try:
        assert asyncio.run(coordination.ping()) is False and dials == [1]
        assert coordination.redis_backing_off()
        assert asyncio.run(coordination.ping()) is False and dials == [1]     # not dialled again
        monkeypatch.setattr(coordination, "_redis_down_until", {"redis://10.255.255.2:6379/0": 0.0})
        assert asyncio.run(coordination.ping()) is False and dials == [1, 1]  # tried again after the back-off
    finally:
        coordination.reset_redis_backoff()


def test_the_client_sends_its_work_to_the_leader(monkeypatch):
    """
    Regression: a follower answered reads before applying the write just made through
    it, so a group created a moment ago was missing from the next list (4 in 30).
    """
    seen: List[str] = []
    leader = {"address": "node-b:2480"}
    down: set = set()

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.netloc.decode()
        if host in down:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path.endswith("/server"):
            seen.append(f"lookup@{host}")
            return httpx.Response(200, json={"ha": {"leader": "b", "leaderAddress": leader["address"]}})
        seen.append(host)
        return httpx.Response(200, json={"result": []})

    client = ArcadeClient("http://node-a:2480,http://node-b:2480,http://node-c:2480", "db", "u", "p")
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(client.command("SELECT 1"))
    asyncio.run(client.command("SELECT 1"))
    assert seen == ["lookup@node-a:2480", "node-b:2480", "node-b:2480"]   # one lookup per LEADER_REFRESH

    # the leader dies: the request fails over to the next node, and the next lookup follows the new leader
    seen.clear()
    down.add("node-b:2480")
    asyncio.run(client.command("SELECT 1"))
    assert seen == ["node-c:2480"]
    leader["address"] = "node-a:2480"
    monkeypatch.setattr(client, "_leader_checked", float("-inf"))
    seen.clear()
    asyncio.run(client.command("SELECT 1"))
    assert seen == ["lookup@node-c:2480", "node-a:2480"]

    # one node: nothing to look up
    single = ArcadeClient("http://only:2480", "db", "u", "p")
    single.http = client.http
    seen.clear()
    asyncio.run(single.command("SELECT 1"))
    assert seen == ["only:2480"]


class _ConflictingClient:
    """Fails the first `conflicts` commits with ArcadeDB's page-version conflict."""

    def __init__(self, conflicts: int) -> None:
        from app.arcadedb import ArcadeDBError
        self.conflicts, self.error = conflicts, ArcadeDBError
        self.log: List[str] = []

    async def begin(self) -> str:
        self.log.append("begin")
        return f"tx{len(self.log)}"

    async def command(self, sql, params=None, *, language="sql", session_id=None):
        self.log.append(sql)
        return []

    async def commit(self, session_id: str) -> None:
        if self.conflicts:
            self.conflicts -= 1
            raise self.error(503, "com.arcadedb.exception.ConcurrentModificationException",
                             "Concurrent modification on page PageId(db/9/8). Please retry the operation")
        self.log.append("commit")

    async def rollback(self, session_id: str) -> None:
        self.log.append("rollback")


def test_a_write_conflict_reruns_the_writes(monkeypatch):
    """Regression: a long import failed at commit because a summary was saved on the same page meanwhile."""
    from app.arcadedb import ArcadeSession
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client = _ConflictingClient(conflicts=2)
    session = ArcadeSession(client)

    async def work():
        await session.execute("UPDATE Artifact SET x = 1")
        return "done"
    assert asyncio.run(session.transaction(work)) == "done"
    assert client.log.count("UPDATE Artifact SET x = 1") == 3 and client.log[-1] == "commit"
    assert client.log.count("begin") == 3          # each attempt in a fresh transaction (a failed commit ends one)


def test_a_conflict_is_not_retried_over_earlier_writes(monkeypatch):
    from app.arcadedb import ArcadeDBError, ArcadeSession
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client = _ConflictingClient(conflicts=1)
    session = ArcadeSession(client)

    async def run():
        await session.execute("INSERT INTO Earlier SET x = 1")      # already in this transaction
        await session.transaction(lambda: session.execute("UPDATE Artifact SET x = 1"))
    with pytest.raises(ArcadeDBError):
        asyncio.run(run())            # rerunning would silently drop the earlier insert
    assert client.log.count("UPDATE Artifact SET x = 1") == 1


async def _no_sleep(_seconds):
    return None


def test_provisioning_waits_for_an_elected_leader_not_just_ready_nodes():
    """
    Regression: on a cold start nodes answer "ready" before the election ends, and
    "list databases" works without a leader, so provisioning raced ahead and failed
    on "create user" (ServerIsNotTheLeader: leader address is unknown).
    """
    answers = iter([{"ha": {"leader": None, "electionStatus": "VOTING_FOR_ME"}},
                    {"ha": {"leader": None}},
                    {"ha": {"leader": "arcadedb-1"}}])

    client = ArcadeClient("http://node-a:2480", "db", "u", "p")      # one node: one answer per call
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(answers))))
    assert asyncio.run(client.cluster_leader()) is None
    assert asyncio.run(client.cluster_leader()) is None
    assert asyncio.run(client.cluster_leader()) == "arcadedb-1"

    standalone, _ = _client(lambda request: httpx.Response(200, json={"version": "26.9.1"}))
    assert asyncio.run(standalone.cluster_leader()) == "standalone"
