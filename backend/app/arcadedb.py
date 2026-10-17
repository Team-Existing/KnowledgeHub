"""
Minimal async client for ArcadeDB's HTTP API, plus a unit-of-work session.

ArcadeSession mirrors how the app used SQLAlchemy sessions: reads run
immediately, the first write opens a server-side transaction, commit() ends
it, and anything uncommitted is rolled back when the request finishes.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TypeVar
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

Record = Dict[str, Any]


class ArcadeDBError(Exception):
    def __init__(self, status: int, exception: str, detail: str) -> None:
        super().__init__(f"{exception}: {detail}")
        self.status = status
        self.exception = exception
        self.detail = detail


class DuplicateKeyError(ArcadeDBError):
    pass


def _clean(value: Any) -> Any:
    """Drop ArcadeDB's @rid/@type/@cat/@in/@out bookkeeping from result rows."""
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if not k.startswith("@")}
    return value


class ArcadeClient:
    """
    One client for a single ArcadeDB server or a cluster.

    `url` may list several nodes, comma-separated. Any cluster node accepts
    reads, writes and transactions (followers forward writes to the leader),
    so requests go to the node that last worked and move on to the next only
    when the request certainly never ran there: the connection was refused,
    or the node answered "not the leader" / "no leader yet" (during an
    election). Timeouts and dropped connections are NOT retried elsewhere:
    the statement may have been applied, and running it twice could
    duplicate a write. A transaction stays on the node where it began.

    With several nodes the client prefers the current leader (looked up at
    most every LEADER_REFRESH seconds). A follower can answer a read before it
    has applied a write just made through it, so a group created a moment ago
    could be missing from the next list; the leader has applied everything it
    acknowledged. ArcadeDB's linearizable reads would fix that too, but cost
    about three seconds per read that follows a write.
    """

    # A dead node must cost seconds, not the whole request timeout: connecting is
    # quick or it isn't happening. Queries themselves may legitimately run longer.
    CONNECT_TIMEOUT = 3.0
    PROBE_TIMEOUT = 2.0
    LEADER_REFRESH = 10.0

    def __init__(self, url: str, database: str, user: str, password: str, timeout: float = 60) -> None:
        self.database = database
        self.nodes = [u.strip().rstrip("/") + "/api/v1" for u in url.split(",") if u.strip()]
        if not self.nodes:
            raise ValueError("ARCADEDB_URL is empty")
        self.http = httpx.AsyncClient(auth=(user, password),
                                      timeout=httpx.Timeout(timeout, connect=self.CONNECT_TIMEOUT))
        self._preferred = 0
        self._session_nodes: Dict[str, str] = {}   # transaction id -> node it lives on
        self._leader_checked = float("-inf")

    @staticmethod
    def _error(response: httpx.Response) -> ArcadeDBError:
        try:
            data = response.json()
        except ValueError:
            data = {"exception": "HTTPError", "detail": response.text}
        exception, detail = data.get("exception") or "", data.get("detail") or data.get("error") or ""
        cls = DuplicateKeyError if "DuplicatedKey" in exception else ArcadeDBError
        return cls(response.status_code, exception, detail)

    @staticmethod
    def _try_elsewhere(response: httpx.Response) -> bool:
        """Answers meaning "this node didn't run it; another node can"."""
        if response.status_code != 503:
            return False
        return any(marker in response.text for marker in ("ServerIsNotTheLeader", "NotLeader", "no leader"))

    def _order(self) -> List[int]:
        n = len(self.nodes)
        return [(self._preferred + i) % n for i in range(n)]

    async def _leader_address(self) -> Optional[str]:
        """The leader's HTTP "host:port", as the cluster reports it; None if unknown."""
        for node in (self.nodes[i] for i in self._order()):
            try:
                response = await self.http.get(f"{node}/server", params={"mode": "cluster"},
                                               timeout=self.PROBE_TIMEOUT)
            except httpx.HTTPError:
                continue
            if response.status_code < 400:
                return (response.json().get("ha") or {}).get("leaderAddress")
        return None

    async def _follow_leader(self) -> None:
        if len(self.nodes) < 2 or time.monotonic() - self._leader_checked < self.LEADER_REFRESH:
            return
        self._leader_checked = time.monotonic()
        address = (await self._leader_address() or "").lower()
        for index, node in enumerate(self.nodes):
            if address and urlsplit(node).netloc.lower() == address:
                self._preferred = index
                return

    async def _post(self, path: str, body: Optional[Record] = None,
                    session_id: Optional[str] = None) -> Tuple[httpx.Response, str]:
        headers = {"arcadedb-session-id": session_id} if session_id else None
        if not session_id:
            await self._follow_leader()
        if session_id:
            node = self._session_nodes.get(session_id, self.nodes[self._preferred])
            try:
                response = await self.http.post(node + path, json=body, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                self._session_nodes.pop(session_id, None)
                raise ArcadeDBError(503, "NodeUnavailable", f"database node went away mid-transaction: {exc}")
            if response.status_code >= 400:
                raise self._error(response)
            return response, node

        last_error: Optional[Exception] = None
        for index in self._order():
            node = self.nodes[index]
            try:
                response = await self.http.post(node + path, json=body, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:   # never reached the node: safe to retry
                last_error = exc
                logger.warning("ArcadeDB node %s unreachable (%s); trying the next one", node, exc)
                continue
            if self._try_elsewhere(response):
                last_error = self._error(response)
                continue
            if response.status_code >= 400:
                raise self._error(response)
            self._preferred = index
            return response, node
        if isinstance(last_error, ArcadeDBError):
            raise last_error
        raise ArcadeDBError(503, "NoNodeAvailable", f"no ArcadeDB node reachable ({last_error})")

    async def command(
        self, command: str, params: Optional[Record] = None, *,
        language: str = "sql", session_id: Optional[str] = None,
    ) -> List[Record]:
        response, _ = await self._post(
            f"/command/{self.database}",
            # limit -1: the HTTP API otherwise truncates results to 20,000 rows silently
            {"language": language, "command": command, "params": params or {}, "limit": -1},
            session_id,
        )
        return [_clean(row) for row in response.json().get("result", [])]

    async def begin(self) -> str:
        response, node = await self._post(f"/begin/{self.database}")
        session_id = response.headers["arcadedb-session-id"]
        self._session_nodes[session_id] = node
        return session_id

    async def commit(self, session_id: str) -> None:
        try:
            await self._post(f"/commit/{self.database}", session_id=session_id)
        finally:
            self._session_nodes.pop(session_id, None)

    async def rollback(self, session_id: str) -> None:
        try:
            await self._post(f"/rollback/{self.database}", session_id=session_id)
        finally:
            self._session_nodes.pop(session_id, None)

    async def server_command(self, command: str) -> Any:
        response, _ = await self._post("/server", {"command": command})
        return response.json().get("result")

    async def database_exists(self) -> bool:
        for node in (self.nodes[i] for i in self._order()):
            try:
                response = await self.http.get(f"{node}/exists/{self.database}")
            except httpx.HTTPError:
                continue
            if response.status_code < 300:
                return bool(response.json().get("result"))
            raise self._error(response)
        raise ArcadeDBError(503, "NoNodeAvailable", "no ArcadeDB node reachable")

    async def ensure_database(self) -> None:
        """
        Create the database if it's missing. Only root may create databases,
        so with a dedicated app user (the clustered deployment) the database
        must already exist; scripts/provision_arcadedb.py creates it.
        """
        if await self.database_exists():
            return
        try:
            await self.server_command(f"create database {self.database}")
            logger.info("Created ArcadeDB database %s", self.database)
        except ArcadeDBError as exc:
            if "already exists" in exc.detail.lower():
                return
            if "only root" in exc.detail.lower():
                raise ArcadeDBError(exc.status, exc.exception,
                                    f"database '{self.database}' does not exist and this user can't create it; "
                                    "run the provisioning step (python -m scripts.provision_arcadedb) first")
            raise

    async def cluster_leader(self) -> Optional[str]:
        """
        The cluster's current leader, "standalone" for a server without HA, or None
        while an election is still running (server commands such as create user
        fail until then). Needs credentials that may read server status (root).
        """
        for node in (self.nodes[i] for i in self._order()):
            try:
                response = await self.http.get(f"{node}/server", params={"mode": "cluster"},
                                               timeout=self.PROBE_TIMEOUT)
            except httpx.HTTPError:
                continue
            if response.status_code >= 400:
                continue
            ha = response.json().get("ha")
            if not ha:
                return "standalone"
            if ha.get("leader"):
                return ha["leader"]
        return None

    async def node_status(self) -> List[Dict[str, Any]]:
        """
        Readiness of every configured node (for /health), probed in parallel with
        a short timeout: the load balancer's health check must answer quickly
        even when a node is down, or it would take healthy app instances out.
        """
        async def probe(node: str) -> Dict[str, Any]:
            try:
                response = await self.http.get(f"{node}/ready", timeout=self.PROBE_TIMEOUT)
                ready = response.status_code < 300
            except httpx.HTTPError:
                ready = False
            return {"node": node.removesuffix("/api/v1"), "ready": ready}
        return list(await asyncio.gather(*(probe(n) for n in self.nodes)))

    async def ready(self) -> bool:
        return any(n["ready"] for n in await self.node_status())

    async def aclose(self) -> None:
        await self.http.aclose()


T = TypeVar("T")


def is_write_conflict(exc: Exception) -> bool:
    """ArcadeDB's "someone else changed this page first; retry the operation"."""
    return isinstance(exc, ArcadeDBError) and "ConcurrentModification" in f"{exc.exception} {exc.detail}"


class ArcadeSession:
    """One per request. Use query() for reads and execute() for writes."""

    WRITE_ATTEMPTS = 4

    def __init__(self, client: ArcadeClient) -> None:
        self.client = client
        self._session_id: Optional[str] = None

    async def query(self, sql: str, params: Optional[Record] = None) -> List[Record]:
        # inside an open transaction reads see its uncommitted writes
        return await self.client.command(sql, params, session_id=self._session_id)

    async def query_one(self, sql: str, params: Optional[Record] = None) -> Optional[Record]:
        rows = await self.query(sql, params)
        return rows[0] if rows else None

    async def scalar(self, sql: str, params: Optional[Record] = None, default: Any = None) -> Any:
        row = await self.query_one(sql, params)
        return next(iter(row.values()), default) if row else default

    async def execute(self, sql: str, params: Optional[Record] = None) -> List[Record]:
        if self._session_id is None:
            self._session_id = await self.client.begin()
        try:
            return await self.client.command(sql, params, session_id=self._session_id)
        except ArcadeDBError:
            # a failed statement leaves the server transaction unusable
            await self.rollback()
            raise

    async def commit(self) -> None:
        if self._session_id is not None:
            session_id, self._session_id = self._session_id, None
            await self.client.commit(session_id)

    async def transaction(self, work: Callable[[], Awaitable[T]]) -> T:
        """
        Run `work` (writes, no commit of its own) and commit it, running it again
        after a write conflict. ArcadeDB checks concurrency per storage page, so two
        transactions writing different records on the same page conflict and the one
        that commits second is told to retry: a long import failed that way when a
        summary was saved meanwhile. Retried only when no transaction was already
        open here, since rolling back would also undo the writes made before it.
        """
        retryable = self._session_id is None
        for attempt in range(1, self.WRITE_ATTEMPTS + 1):
            try:
                result = await work()
                await self.commit()
                return result
            except ArcadeDBError as exc:
                await self.rollback()
                if not (retryable and is_write_conflict(exc)) or attempt == self.WRITE_ATTEMPTS:
                    raise
                logger.warning("Write conflict, retrying (%d of %d)", attempt + 1, self.WRITE_ATTEMPTS)
                await asyncio.sleep(0.05 * 2 ** attempt + random.uniform(0, 0.1))
        raise AssertionError("unreachable")

    async def rollback(self) -> None:
        if self._session_id is not None:
            session_id, self._session_id = self._session_id, None
            try:
                await self.client.rollback(session_id)
            except (ArcadeDBError, httpx.HTTPError) as exc:
                logger.warning("ArcadeDB rollback failed: %s", exc)

    async def close(self) -> None:
        await self.rollback()
