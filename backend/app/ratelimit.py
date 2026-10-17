"""
Throttles password guessing on sign-in and mass sign-ups.

Fixed-window counters, keyed by client address (and, for sign-in, the
username tried). In worker mode (REDIS_URL set) they live in Redis, so every
app instance behind the load balancer counts the same attempts; otherwise
they are in memory, which is enough for a single process.

Only *failed* sign-ins count, and a success clears that address's counter for
that account, so someone who mistypes a password once isn't penalised. The
per-account limit is keyed by address too: a stranger can't lock a user out
from elsewhere by failing on purpose.

If Redis is unreachable the limiter lets requests through (and logs it):
sign-in must keep working while the queue is down, as everything else does.
Each operation gets REDIS_TIMEOUT in all, and after a failure Redis is left
alone for coordination.REDIS_BACKOFF seconds: with the Redis container gone,
even resolving its name can take seconds, and every sign-in would pay that.

AUTH_RATE_LIMIT=false turns it off (the test suite does; its own tests turn
it back on).
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from typing import Awaitable, Callable, Dict, Optional, Tuple, TypeVar

from fastapi import HTTPException, Request

from app import coordination

logger = logging.getLogger(__name__)

LOGIN_WINDOW = 15 * 60
LOGIN_FAILURES_PER_ACCOUNT = 5      # per address and username
LOGIN_FAILURES_PER_ADDRESS = 30     # per address, any username
REGISTER_WINDOW = 60 * 60
REGISTRATIONS_PER_ADDRESS = 20

_local: Dict[str, Tuple[int, float]] = {}    # key -> (count, window ends at)
_LOCAL_MAX_KEYS = 10_000

T = TypeVar("T")


def enabled() -> bool:
    return os.getenv("AUTH_RATE_LIMIT", "true").strip().lower() not in ("false", "0", "no", "off")


def client_address(request: Request) -> str:
    # behind the load balancer, uvicorn's --proxy-headers has already replaced this
    # with the address Caddy saw (Caddy discards X-Forwarded-For sent by clients)
    return request.client.host if request.client else "unknown"


def reset() -> None:
    """Forget every in-memory counter and any Redis back-off (tests)."""
    _local.clear()
    coordination.reset_redis_backoff()


async def _guarded(op: Callable[[], Awaitable[T]], what: str) -> Optional[T]:
    """
    Run op() within REDIS_TIMEOUT. If Redis fails (or is in back-off), fail open:
    return None, so the request goes ahead unthrottled. A 429 passes through.
    """
    if coordination.redis_backing_off():
        return None
    try:
        return await asyncio.wait_for(op(), coordination.REDIS_TIMEOUT)
    except HTTPException:
        raise
    except Exception as exc:
        coordination.note_redis_failure()
        logger.warning("Rate limiter unavailable (%s), not enforcing it for %.0fs: %r",
                       what, coordination.REDIS_BACKOFF, exc)
        return None


def _prune(now: float) -> None:
    if len(_local) > _LOCAL_MAX_KEYS:
        for key in [k for k, (_, ends) in _local.items() if ends <= now]:
            del _local[key]


async def _count(key: str) -> Tuple[int, int]:
    """(attempts so far, seconds until the window resets)."""
    if coordination.worker_mode():
        client = coordination._redis()
        count, ttl = await client.get(f"kh:rl:{key}"), await client.ttl(f"kh:rl:{key}")
        return int(count or 0), max(int(ttl), 1)
    count, ends = _local.get(key, (0, 0.0))
    now = time.monotonic()
    if ends <= now:
        return 0, 0
    return count, max(math.ceil(ends - now), 1)


async def _hit(key: str, window: int) -> None:
    if coordination.worker_mode():
        client = coordination._redis()
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(f"kh:rl:{key}")
            pipe.expire(f"kh:rl:{key}", window, nx=True)
            await pipe.execute()
        return
    now = time.monotonic()
    _prune(now)
    count, ends = _local.get(key, (0, 0.0))
    if ends <= now:
        count, ends = 0, now + window
    _local[key] = (count + 1, ends)


async def _clear(key: str) -> None:
    if coordination.worker_mode():
        await coordination._redis().delete(f"kh:rl:{key}")
        return
    _local.pop(key, None)


def _too_many(retry_after: int) -> HTTPException:
    minutes = max(1, math.ceil(retry_after / 60))
    return HTTPException(
        status_code=429,
        detail=f"Too many attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
        headers={"Retry-After": str(retry_after)},
    )


async def check(*limits: Tuple[str, int]) -> None:
    """Raise 429 if any (key, limit) has already been reached."""
    if not enabled():
        return

    async def op() -> None:
        for key, limit in limits:
            count, retry_after = await _count(key)
            if count >= limit:
                raise _too_many(retry_after)
    await _guarded(op, "check")


async def hit(*keys: str, window: int) -> None:
    if not enabled():
        return

    async def op() -> None:
        for key in keys:
            await _hit(key, window)
    await _guarded(op, "count")


async def clear(key: str) -> None:
    if not enabled():
        return
    await _guarded(lambda: _clear(key), "clear")


def login_keys(request: Request, username_key: str) -> Tuple[str, str]:
    address = client_address(request)
    return f"login:{address}:{username_key}", f"login:{address}"


def register_key(request: Request) -> str:
    return f"register:{client_address(request)}"
