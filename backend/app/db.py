from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy import JSON, Column, ForeignKey, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/knowledge.db")

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# User / Auth
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=_uuid)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="member")
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# Core Knowledge Tables
# ---------------------------------------------------------------------------

class Artifact(Base):
    """Source documents or transcripts ingested by the user."""
    __tablename__ = "artifacts"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    source = Column(String, default="manual")
    source_type = Column(String, default="manual")
    author = Column(String, default="unknown")
    tags = Column(JSON, default=list)
    extraction_engine = Column(String, default="regex")
    created_at = Column(String, nullable=False)
    metadata_ = Column("metadata", JSON, default=dict)


class KnowledgeItem(Base):
    """Extracted knowledge: decisions, risks, action items, etc."""
    __tablename__ = "knowledge_items"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    artifact_id = Column(String, ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    title = Column(String, nullable=False)
    type = Column(String, nullable=False)  # decision, risk, action-item, etc.
    author = Column(String, default="unknown")
    date = Column(String, nullable=False)
    tags = Column(JSON, default=list)
    details = Column(JSON, default=dict)
    extraction_engine = Column(String, default="regex")
    embedding = Column(JSON, nullable=True)
    embedding_provider = Column(String, nullable=True)
    embedding_dims = Column(Integer, nullable=True)
    review_status = Column(String, default="pending")
    review_note = Column(Text, default="")


class Relationship(Base):
    """Graph relationships between knowledge items and artifacts."""
    __tablename__ = "relationships"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    from_id = Column(String, nullable=False, index=True)
    to_id = Column(String, nullable=False, index=True)
    type = Column(String, nullable=False)  # CONTAINS, RELATED_TO, etc.


class CrossLink(Base):
    """Cross-source links between knowledge items from different artifacts."""
    __tablename__ = "cross_links"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    item_id_a = Column(String, ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False, index=True)
    item_id_b = Column(String, ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False, index=True)
    score = Column(String, nullable=False)  # Similarity score as string


# ---------------------------------------------------------------------------
# Playbooks
# ---------------------------------------------------------------------------

class Playbook(Base):
    """Curated playbooks or workflows."""
    __tablename__ = "playbooks"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    steps = Column(JSON, default=list)
    category = Column(String, default="general")


# ---------------------------------------------------------------------------
# Summaries (for GraphRAG summary index)
# ---------------------------------------------------------------------------

class ArtifactSummary(Base):
    """LLM-generated summary of an artifact for the summary index."""
    __tablename__ = "artifact_summaries"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    artifact_id = Column(String, ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    summary = Column(Text, nullable=False)
    embedding = Column(JSON, nullable=True)
    embedding_provider = Column(String, nullable=True)
    embedding_dims = Column(Integer, nullable=True)
    created_at = Column(String, nullable=False)


# ---------------------------------------------------------------------------
# Observability / Query Logs
# ---------------------------------------------------------------------------

class QueryLog(Base):
    """Persists every GraphRAG query for observability."""
    __tablename__ = "query_logs"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    question = Column(Text, nullable=False)
    sub_queries = Column(JSON, default=list)
    hyde_doc = Column(Text, nullable=True)
    route = Column(String, nullable=True)
    retrieval_mode = Column(String, nullable=True)
    llm_provider = Column(String, nullable=True)
    embedding_provider = Column(String, nullable=True)
    context_node_ids = Column(JSON, default=list)
    citations = Column(JSON, default=list)
    answer_snippet = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(String, nullable=False)


# ---------------------------------------------------------------------------
# Session Helper
# ---------------------------------------------------------------------------

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# Database Initialization
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Create all tables if they don't exist."""
    async with engine.begin() as conn:
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)