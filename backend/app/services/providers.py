"""
Provider abstraction layer — Phase 1.

Defines LLMProvider and EmbeddingProvider abstract interfaces and their
concrete implementations. Callers should use get_llm_provider() and
get_embedding_provider() to obtain the active singleton.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# Prevent sentence-transformers / HuggingFace from phoning home to
# huggingface.co on every cold start to check for model updates.
# The model is cached locally after first download; this enforces offline
# operation and is required for the local-first / airplane-mode guarantee.
# Set before any HF library is imported so the flag is always respected.
if not os.getenv("TRANSFORMERS_OFFLINE"):
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
if not os.getenv("HF_DATASETS_OFFLINE"):
    os.environ["HF_DATASETS_OFFLINE"] = "1"

# ── env ───────────────────────────────────────────────────────────────────────
LLM_PROVIDER       = os.getenv("LLM_PROVIDER", "ollama").lower()
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local").lower()

OLLAMA_BASE  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

LOCAL_EMBED_MODEL  = os.getenv("LOCAL_EMBED_MODEL", "all-MiniLM-L6-v2")


# ═════════════════════════════════════════════════════════════════════════════
# LLM interface
# ═════════════════════════════════════════════════════════════════════════════

class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: List[dict],
        temperature: float = 0,
        max_tokens: int = 1000,
        json_mode: bool = False,
    ) -> Optional[str]: ...

    @property
    @abstractmethod
    def is_local(self) -> bool: ...

    @property
    @abstractmethod
    def name(self) -> str: ...


class OllamaProvider(LLMProvider):
    """Default local LLM via Ollama HTTP API."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or OLLAMA_MODEL

    @property
    def is_local(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    async def chat(
        self,
        messages: List[dict],
        temperature: float = 0,
        max_tokens: int = 1000,
        json_mode: bool = False,
    ) -> Optional[str]:
        try:
            import httpx
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            if json_mode:
                payload["format"] = "json"
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=5.0)
            ) as client:
                r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
                r.raise_for_status()
                return r.json()["message"]["content"].strip()
        except Exception as exc:
            logger.warning("OllamaProvider.chat() failed: %s", exc)
            return None





# ═════════════════════════════════════════════════════════════════════════════
# Embedding interface
# ═════════════════════════════════════════════════════════════════════════════

class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: List[str]) -> List[Optional[List[float]]]: ...

    @property
    @abstractmethod
    def dimensions(self) -> int: ...

    @property
    @abstractmethod
    def is_local(self) -> bool: ...

    @property
    @abstractmethod
    def name(self) -> str: ...


class LocalSentenceTransformerProvider(EmbeddingProvider):
    """Default local embeddings via sentence-transformers (all-MiniLM-L6-v2)."""

    _model: Any = None

    def _load(self) -> Any:
        if self.__class__._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self.__class__._model = SentenceTransformer(LOCAL_EMBED_MODEL)
                logger.info("sentence-transformers loaded: %s", LOCAL_EMBED_MODEL)
            except Exception as exc:
                logger.warning("sentence-transformers unavailable: %s", exc)
        return self.__class__._model

    @property
    def dimensions(self) -> int:
        return 384

    @property
    def is_local(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return f"local:{LOCAL_EMBED_MODEL}"

    async def embed(self, texts: List[str]) -> List[Optional[List[float]]]:
        model = self._load()
        if model is None:
            return [None] * len(texts)
        try:
            vecs = model.encode([t[:2000] for t in texts], normalize_embeddings=True)
            return [v.tolist() for v in vecs]
        except Exception as exc:
            logger.warning("LocalSentenceTransformerProvider.embed() failed: %s", exc)
            return [None] * len(texts)





# ═════════════════════════════════════════════════════════════════════════════
# Singletons — resolved once at import time
# ═════════════════════════════════════════════════════════════════════════════

def _normalize_provider(name: Optional[str], default: str) -> str:
    if not name:
        return default
    return name.strip().lower()


def _resolve_llm(workspace: Optional[Any] = None) -> LLMProvider:
    configured_model = getattr(workspace, "ollama_model", None) if workspace is not None else None
    logger.info("LLM provider: Ollama (%s)", configured_model or OLLAMA_MODEL)
    return OllamaProvider(configured_model)


def _resolve_embedding(workspace: Optional[Any] = None) -> EmbeddingProvider:
    logger.info("Embedding provider: local sentence-transformers (%s)", LOCAL_EMBED_MODEL)
    return LocalSentenceTransformerProvider()


def get_llm_provider(workspace: Optional[Any] = None) -> LLMProvider:
    return _resolve_llm(workspace)


def get_embedding_provider(workspace: Optional[Any] = None) -> EmbeddingProvider:
    return _resolve_embedding(workspace)
