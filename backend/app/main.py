from __future__ import annotations

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True) or find_dotenv())

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import coordination, migrations
from app.auth import check_secret_key
from app.db import close_db, get_client, init_db, migrate_on_startup
from app.middleware import BodySizeLimitMiddleware
from app.routers import admin, auth, connectors, graphrag, groups, health, knowledge, lineage, models
from app.services.file_ingestion import MAX_UPLOAD_BYTES
from app.services import retention
from app.services.connectors.sync import QueueUnavailable
from app.services.providers import get_embedding_provider

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    check_secret_key()   # refuse to run with a forgeable JWT signing key
    # vector indexes are sized to the active embedding model
    embedder = get_embedding_provider()
    await init_db(embedder.dimensions, embedder.name)
    if migrate_on_startup():
        await migrations.run(get_client())
    # The inactivity clean-up: Celery beat schedules it in worker mode (REDIS_URL set);
    # otherwise this process runs it now and then every RETENTION_CHECK_HOURS.
    retention_task = None if coordination.worker_mode() else asyncio.create_task(retention.loop(get_client()))
    try:
        yield
    finally:
        if retention_task:
            retention_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retention_task
        await close_db()


app = FastAPI(title="Knowledge Hubs", lifespan=lifespan)


@app.exception_handler(QueueUnavailable)
async def queue_unavailable(request, exc):   # starting or deleting a sync while Redis is down
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503, content={"detail": "The background queue is unavailable; try again shortly"})

# 64 KB headroom for multipart boundaries and the other form fields
app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_UPLOAD_BYTES + 64 * 1024)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:4200", "http://127.0.0.1:4200",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(groups.router)
app.include_router(models.router)
app.include_router(health.router)
app.include_router(connectors.router)
# graphrag and lineage before knowledge: knowledge ends with the GET /knowledge/{item_id} catch-all
app.include_router(graphrag.router)
app.include_router(lineage.router)
app.include_router(knowledge.router)
