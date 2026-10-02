"""
Coordination between app instances and background work.

Two modes, chosen by REDIS_URL:

  worker (REDIS_URL set)   connector syncs and the inactivity clean-up run on
                           the Celery worker (app/worker.py); locks live in
                           Redis, so every app instance behind the load
                           balancer agrees on what is running.
  in-process (unset)       single-instance development: work runs inside the
                           API process and locks are in memory, as before.

Locks expire unless renewed, so a crashed worker can't hold one forever:
the connector then shows "interrupted" until Celery redelivers the task
(acks_late) and the sync runs again, which is safe because syncs are
idempotent (unchanged documents are skipped by content hash).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SYNC_LOCK_TTL = 120        # seconds; renewed every SYNC_LOCK_TTL / 4 while a sync runs
RETENTION_LOCK_TTL = 3600
# Redis being down must fail fast, never hang: /health would otherwise exceed the
# load balancer's check and take every app instance out of rotation.
REDIS_TIMEOUT = 2.0

# compare-and-delete / compare-and-extend, so a lock is only touched by its holder
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_EXTEND = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"


def redis_url() -> Optional[str]:
    return os.getenv("REDIS_URL") or None


def worker_mode() -> bool:
    return redis_url() is not None


# ── locks ───────────────────────────────────────────────────────────────────

_local: Dict[str, Tuple[str, float]] = {}       # name -> (token, expires at)
_redis_clients: Dict[int, object] = {}          # one client per event loop (workers use asyncio.run per task)


def _redis():
    import redis.asyncio as aioredis
    loop_id = id(asyncio.get_running_loop())
    client = _redis_clients.get(loop_id)
    if client is None:
        client = aioredis.from_url(redis_url(), decode_responses=True,
                                   socket_connect_timeout=REDIS_TIMEOUT, socket_timeout=REDIS_TIMEOUT)
        _redis_clients[loop_id] = client
    return client


async def acquire(name: str, ttl: int) -> Optional[str]:
    """Take the lock; returns a token, or None if someone else holds it."""
    token = uuid.uuid4().hex
    if worker_mode():
        ok = await _redis().set(f"kh:lock:{name}", token, nx=True, ex=ttl)
        return token if ok else None
    held = _local.get(name)
    if held and held[1] > time.monotonic():
        return None
    _local[name] = (token, time.monotonic() + ttl)
    return token


async def extend(name: str, token: str, ttl: int) -> bool:
    if worker_mode():
        return bool(await _redis().eval(_EXTEND, 1, f"kh:lock:{name}", token, ttl * 1000))
    held = _local.get(name)
    if held and held[0] == token:
        _local[name] = (token, time.monotonic() + ttl)
        return True
    return False


async def release(name: str, token: str) -> None:
    if worker_mode():
        await _redis().eval(_RELEASE, 1, f"kh:lock:{name}", token)
        return
    if _local.get(name, ("",))[0] == token:
        _local.pop(name, None)


async def is_held(name: str) -> bool:
    if worker_mode():
        return bool(await _redis().exists(f"kh:lock:{name}"))
    held = _local.get(name)
    return bool(held and held[1] > time.monotonic())


@asynccontextmanager
async def held_lock(name: str, ttl: int) -> AsyncIterator[Optional[str]]:
    """
    Hold `name` for the duration of the block, renewing it in the background.
    Yields None (and runs nothing extra) if someone else holds it.
    """
    token = await acquire(name, ttl)
    if token is None:
        yield None
        return

    async def renew() -> None:
        while True:
            await asyncio.sleep(max(1, ttl // 4))
            if not await extend(name, token, ttl):
                logger.warning("Lost lock %s while still working", name)
                return

    renewer = asyncio.create_task(renew())
    try:
        yield token
    finally:
        renewer.cancel()
        try:
            await renewer
        except (asyncio.CancelledError, Exception):
            pass
        await release(name, token)


def sync_lock(connector_id: str) -> str:
    return f"sync:{connector_id}"


async def sync_running(connector_id: str) -> bool:
    return await is_held(sync_lock(connector_id))


async def ping() -> Optional[bool]:
    """Redis reachability for /health: None in in-process mode."""
    if not worker_mode():
        return None
    try:
        return bool(await asyncio.wait_for(_redis().ping(), timeout=REDIS_TIMEOUT))
    except Exception:
        return False


# ── dispatch ────────────────────────────────────────────────────────────────

async def dispatch_sync(space_id: str, connector_id: str, actor_id: str, background=None) -> str:
    """
    Start a connector sync: on the Celery worker in worker mode, otherwise as
    a FastAPI background task in this process. Returns "worker" or "in-process".
    """
    if worker_mode():
        from starlette.concurrency import run_in_threadpool
        from app.worker import celery_app
        await run_in_threadpool(celery_app.send_task, "kh.sync_connector",
                                args=[space_id, connector_id, actor_id])
        return "worker"
    from app.services.connectors.sync import run_sync_job
    if background is None:
        raise RuntimeError("in-process mode needs FastAPI BackgroundTasks to run the sync")
    background.add_task(run_sync_job, space_id, connector_id, actor_id)
    return "in-process"
