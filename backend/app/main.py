from __future__ import annotations

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True) or find_dotenv())

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import close_db, init_db
from app.middleware import BodySizeLimitMiddleware
from app.routers import auth, connectors, graphrag, health, knowledge, lineage, models
from app.services.file_ingestion import MAX_UPLOAD_BYTES
from app.services.providers import get_embedding_provider

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # vector indexes are sized to the active embedding model
    embedder = get_embedding_provider()
    await init_db(embedder.dimensions, embedder.name)
    try:
        yield
    finally:
        await close_db()


app = FastAPI(title="Knowledge Hubs", lifespan=lifespan)

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
app.include_router(models.router)
app.include_router(health.router)
app.include_router(connectors.router)
# graphrag and lineage before knowledge: knowledge ends with the GET /knowledge/{item_id} catch-all
app.include_router(graphrag.router)
app.include_router(lineage.router)
app.include_router(knowledge.router)
