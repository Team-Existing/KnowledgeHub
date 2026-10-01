"""
Minimal async client for ArcadeDB's HTTP API, plus a unit-of-work session.

ArcadeSession mirrors how the app used SQLAlchemy sessions: reads run
immediately, the first write opens a server-side transaction, commit() ends
it, and anything uncommitted is rolled back when the request finishes.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

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
    def __init__(self, url: str, database: str, user: str, password: str, timeout: float = 60) -> None:
        self.database = database
        self.http = httpx.AsyncClient(
            base_url=url.rstrip("/") + "/api/v1", auth=(user, password), timeout=timeout,
        )

    async def _post(self, path: str, body: Optional[Record] = None, session_id: Optional[str] = None) -> httpx.Response:
        headers = {"arcadedb-session-id": session_id} if session_id else None
        response = await self.http.post(path, json=body, headers=headers)
        if response.status_code >= 400:
            try:
                data = response.json()
            except ValueError:
                data = {"exception": "HTTPError", "detail": response.text}
            exception, detail = data.get("exception") or "", data.get("detail") or data.get("error") or ""
            cls = DuplicateKeyError if "DuplicatedKey" in exception else ArcadeDBError
            raise cls(response.status_code, exception, detail)
        return response

    async def command(
        self, command: str, params: Optional[Record] = None, *,
        language: str = "sql", session_id: Optional[str] = None,
    ) -> List[Record]:
        response = await self._post(
            f"/command/{self.database}",
            # limit -1: the HTTP API otherwise truncates results to 20,000 rows silently
            {"language": language, "command": command, "params": params or {}, "limit": -1},
            session_id,
        )
        return [_clean(row) for row in response.json().get("result", [])]

    async def begin(self) -> str:
        response = await self._post(f"/begin/{self.database}")
        return response.headers["arcadedb-session-id"]

    async def commit(self, session_id: str) -> None:
        await self._post(f"/commit/{self.database}", session_id=session_id)

    async def rollback(self, session_id: str) -> None:
        await self._post(f"/rollback/{self.database}", session_id=session_id)

    async def server_command(self, command: str) -> Any:
        return (await self._post("/server", {"command": command})).json().get("result")

    async def ensure_database(self) -> None:
        try:
            await self.server_command(f"create database {self.database}")
            logger.info("Created ArcadeDB database %s", self.database)
        except ArcadeDBError as exc:
            if "already exists" not in exc.detail.lower():
                raise

    async def ready(self) -> bool:
        try:
            return (await self.http.get("/ready")).status_code < 300
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self.http.aclose()


class ArcadeSession:
    """One per request. Use query() for reads and execute() for writes."""

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

    async def rollback(self) -> None:
        if self._session_id is not None:
            session_id, self._session_id = self._session_id, None
            try:
                await self.client.rollback(session_id)
            except (ArcadeDBError, httpx.HTTPError) as exc:
                logger.warning("ArcadeDB rollback failed: %s", exc)

    async def close(self) -> None:
        await self.rollback()
