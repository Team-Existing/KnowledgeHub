"""
Embedding shim — routes all calls through the active EmbeddingProvider.

Public API is unchanged: callers import `embed`, `embed_batch`, and
`EMBEDDING_DIM` as before. Provider selection is handled in providers.py.
"""
from __future__ import annotations

from typing import Any, List, Optional

from app.services.providers import get_embedding_provider

# Expose dimension and provider name as module-level constants
# (used to size the ArcadeDB vector indexes and for embedding provenance)
EMBEDDING_DIM: int = get_embedding_provider().dimensions
EMBEDDING_PROVIDER_NAME: str = get_embedding_provider().name


async def embed(text: str, workspace: Optional[Any] = None) -> Optional[List[float]]:
    """Embed a single string. Returns None on failure."""
    results = await get_embedding_provider(workspace).embed([text])
    return results[0] if results else None


async def embed_batch(texts: List[str], workspace: Optional[Any] = None) -> List[Optional[List[float]]]:
    """Embed a list of strings. Returns a list of the same length."""
    return await get_embedding_provider(workspace).embed(texts)
