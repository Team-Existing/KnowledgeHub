"""
Celery worker and scheduler (worker mode: REDIS_URL set).

    celery -A app.worker worker --loglevel=INFO      # runs connector syncs and the clean-up
    celery -A app.worker beat   --loglevel=INFO      # exactly ONE of these: schedules the clean-up

Tasks are acknowledged only after they finish (acks_late) and are put back
on the queue if the worker process dies (reject_on_worker_lost), so a crash
or a redeploy in the middle of a sync means it runs again rather than being
lost. That is safe: syncs skip unchanged documents, and both tasks hold a
Redis lock (app/coordination.py) so a redelivered or duplicate task never
runs alongside another copy.

Each task runs its own event loop with its own database client; the API's
instances and the worker share nothing but ArcadeDB and Redis.
"""
from __future__ import annotations

import asyncio
import logging
import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True) or find_dotenv())

from celery import Celery  # noqa: E402

from app.services import retention  # noqa: E402

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("knowledge_hubs", broker=REDIS_URL)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,        # one long sync shouldn't hold others hostage on the same worker
    task_ignore_result=True,
    # A task whose worker died without finishing it (crash, kill -9, lost host) goes back on
    # the queue after this many seconds. Keep it longer than your longest sync.
    broker_transport_options={"visibility_timeout": int(os.getenv("CELERY_VISIBILITY_TIMEOUT", "7200"))},
    timezone="UTC",
    beat_schedule={
        "inactivity-clean-up": {
            "task": "kh.retention",
            "schedule": retention.check_interval().total_seconds(),
        },
    },
)


async def _with_client(work):
    from app.db import client_from_env
    client = client_from_env()
    try:
        return await work(client)
    finally:
        await client.aclose()


@celery_app.task(name="kh.sync_connector")
def sync_connector(space_id: str, connector_id: str, actor_id: str) -> None:
    from app.services.connectors.sync import run_sync_job

    asyncio.run(_with_client(lambda client: run_sync_job(space_id, connector_id, actor_id, client=client)))


@celery_app.task(name="kh.retention")
def run_retention() -> dict:
    if not retention.enabled():
        return {"skipped": "RETENTION_ENABLED=false"}
    from app.arcadedb import ArcadeSession

    async def work(client):
        session = ArcadeSession(client)
        try:
            return await retention.run(session)
        finally:
            await session.close()

    report = asyncio.run(_with_client(work))
    logger.info("Inactivity clean-up: %s", report)
    return report
