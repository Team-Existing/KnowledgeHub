"""
Provider abstraction layer.

Defines LLMProvider and EmbeddingProvider abstract interfaces and their
concrete implementations. Callers should use get_llm_provider() and
get_embedding_provider() to obtain the active provider.

LLMs: the local models served by Ollama, and the models of one OpenAI-compatible
API (a cloud service such as OpenAI, or a self-hosted server such as vLLM),
configured with LLM_API_*. A model id is an Ollama tag ("llama3.1:8b") or
"api/" + the API's model name ("api/gpt-4.1-mini").
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextvars import ContextVar
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

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
# Ollama's default context (2-4k tokens) silently truncates long prompts;
# extraction sends up to ~3k tokens of input plus a 1.5k-token reply.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
# How long Ollama keeps the model in memory after a call. Its default (5 minutes)
# unloads it between quiet spells, and reloading an 8B model costs 30-60 s on a CPU.
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")

LOCAL_EMBED_MODEL  = os.getenv("LOCAL_EMBED_MODEL", "all-MiniLM-L6-v2")

# false: no Ollama at all (a hosted deployment that only uses LLM_API_*)
OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")

# An OpenAI-compatible chat completions API. Nothing is sent to it unless both the
# URL and the model list are set. Deliberately not OPENAI_API_KEY: a key that happens
# to be in the environment must never start sending the knowledge base somewhere.
API_PREFIX = "api/"
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "").strip().rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_API_MODELS = [m.strip() for m in os.getenv("LLM_API_MODELS", "").split(",") if m.strip()] \
    if LLM_API_BASE_URL else []
LLM_API_NAME = os.getenv("LLM_API_NAME", "").strip() or urlparse(LLM_API_BASE_URL).hostname or "the LLM API"
LLM_API_TIMEOUT = float(os.getenv("LLM_API_TIMEOUT_SECONDS", "120"))

if LLM_API_BASE_URL and not LLM_API_MODELS:
    logger.warning("LLM_API_BASE_URL is set but LLM_API_MODELS is empty: no API model is offered")


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
        json_mode: bool | Dict[str, Any] = False,
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
        json_mode: bool | Dict[str, Any] = False,
    ) -> Optional[str]:
        """json_mode: True for any JSON object, or a JSON schema the reply must follow (structured output)."""
        try:
            import httpx
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "keep_alive": OLLAMA_KEEP_ALIVE,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                    "num_ctx": OLLAMA_NUM_CTX,
                },
            }
            if isinstance(json_mode, dict):
                payload["format"] = json_mode
            elif json_mode:
                payload["format"] = "json"
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=5.0)
            ) as client:
                r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
                r.raise_for_status()
                body = r.json()
            logger.info("LLM %s: %.1f s, %s prompt + %s reply tokens", self.model,
                        (body.get("total_duration") or 0) / 1e9, body.get("prompt_eval_count"), body.get("eval_count"))
            if body.get("done_reason") == "length":
                logger.warning("LLM %s stopped at the %d-token reply limit; the reply is cut short",
                               self.model, max_tokens)
            return body["message"]["content"].strip()
        except Exception as exc:
            logger.warning("OllamaProvider.chat() failed: %s", exc)
            return None


class OpenAICompatibleProvider(LLMProvider):
    """A model behind the OpenAI-compatible chat completions API at LLM_API_BASE_URL."""

    # Models that rejected max_tokens or temperature (OpenAI's reasoning models want
    # max_completion_tokens and no temperature), or a JSON schema: sent the accepted form from then on.
    _newer_params: Set[str] = set()
    _no_schema: Set[str] = set()

    def __init__(self, model: str):
        self.model = model

    @property
    def is_local(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return f"api:{self.model}"

    def _payload(self, messages: List[dict], temperature: float, max_tokens: int,
                 json_mode: bool | Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        if self.model in self._newer_params:
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
            payload["temperature"] = temperature
        if isinstance(json_mode, dict) and self.model not in self._no_schema:
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "reply", "schema": json_mode}}
        elif json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _adapt(self, error: str, json_mode: bool | Dict[str, Any]) -> bool:
        """After a 400: whether a different form of the request may be accepted."""
        error = error.lower()
        if self.model not in self._newer_params and ("max_completion_tokens" in error or "temperature" in error):
            self._newer_params.add(self.model)
            return True
        if isinstance(json_mode, dict) and self.model not in self._no_schema \
                and ("json_schema" in error or "response_format" in error):
            self._no_schema.add(self.model)
            return True
        return False

    async def chat(
        self,
        messages: List[dict],
        temperature: float = 0,
        max_tokens: int = 1000,
        json_mode: bool | Dict[str, Any] = False,
    ) -> Optional[str]:
        """json_mode: True for any JSON object, or a JSON schema the reply must follow (structured output)."""
        import httpx
        headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=LLM_API_TIMEOUT, write=10.0, pool=5.0)
            ) as client:
                for attempt in range(4):
                    r = await client.post(f"{LLM_API_BASE_URL}/chat/completions", headers=headers,
                                          json=self._payload(messages, temperature, max_tokens, json_mode))
                    if r.status_code == 400 and self._adapt(r.text, json_mode):
                        continue
                    if (r.status_code == 429 or r.status_code >= 500) and attempt < 2:
                        try:   # rate limited or briefly down: wait as asked, within reason
                            wait = float(r.headers.get("retry-after", "2"))
                        except ValueError:
                            wait = 2.0
                        await asyncio.sleep(min(max(wait, 0.5), 10.0))
                        continue
                    break
                if r.status_code >= 400:
                    logger.warning("LLM API %s: HTTP %d: %s", self.model, r.status_code, r.text[:300])
                    return None
                body = r.json()
            choice = body["choices"][0]
            usage = body.get("usage") or {}
            logger.info("LLM %s (%s): %.1f s, %s prompt + %s reply tokens", self.model, LLM_API_NAME,
                        time.monotonic() - started, usage.get("prompt_tokens"), usage.get("completion_tokens"))
            if choice.get("finish_reason") == "length":
                logger.warning("LLM %s stopped at the %d-token reply limit; the reply is cut short",
                               self.model, max_tokens)
            content = (choice.get("message") or {}).get("content")
            return content.strip() if content else None
        except Exception as exc:
            logger.warning("OpenAICompatibleProvider.chat() failed: %s", exc)
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
    _load_lock = threading.Lock()  # encode() now runs in worker threads

    def _load(self) -> Any:
        with self._load_lock:
            if self.__class__._model is not None:
                return self.__class__._model
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

    def _encode(self, texts: List[str]) -> List[Optional[List[float]]]:
        model = self._load()
        if model is None:
            return [None] * len(texts)
        try:
            vecs = model.encode([t[:2000] for t in texts], normalize_embeddings=True)
            return [v.tolist() for v in vecs]
        except Exception as exc:
            logger.warning("LocalSentenceTransformerProvider.embed() failed: %s", exc)
            return [None] * len(texts)

    async def embed(self, texts: List[str]) -> List[Optional[List[float]]]:
        # encode() is CPU-bound and synchronous; run it (and the first-call
        # model load) in a worker thread so it doesn't stall the event loop.
        return await asyncio.to_thread(self._encode, texts)





# ═════════════════════════════════════════════════════════════════════════════
# Resolution — per call, from the active model
# ═════════════════════════════════════════════════════════════════════════════

def _normalize_provider(name: Optional[str], default: str) -> str:
    if not name:
        return default
    return name.strip().lower()


def api_model_ids() -> List[str]:
    """The LLM API's models as model ids ("api/<name>"); empty when no API is configured."""
    return [API_PREFIX + m for m in LLM_API_MODELS]


def is_api_model(model_id: Optional[str]) -> bool:
    return bool(model_id) and model_id in api_model_ids()


def default_model() -> str:
    """For calls with no user's choice: LLM_DEFAULT_MODEL, else OLLAMA_MODEL, else the first API model."""
    configured = os.getenv("LLM_DEFAULT_MODEL", "").strip()
    if configured:
        return configured
    if OLLAMA_ENABLED or not LLM_API_MODELS:
        return OLLAMA_MODEL
    return api_model_ids()[0]


# The signed-in user's chosen model for the current request (or background job).
# Set by auth.get_current_user and by jobs that run on a user's behalf.
_active_model: ContextVar[Optional[str]] = ContextVar("active_llm_model", default=None)


def use_model(model_id: Optional[str]) -> None:
    """Route this request's LLM calls to `model_id` (None = default_model())."""
    _active_model.set(model_id or None)


def active_model() -> str:
    return _active_model.get() or default_model()


def provider_for(model_id: str) -> LLMProvider:
    if model_id.startswith(API_PREFIX):
        return OpenAICompatibleProvider(model_id[len(API_PREFIX):])
    return OllamaProvider(model_id)


def _resolve_llm(workspace: Optional[Any] = None) -> LLMProvider:
    configured_model = getattr(workspace, "ollama_model", None) if workspace is not None else None
    return provider_for(configured_model or active_model())


def _resolve_embedding(workspace: Optional[Any] = None) -> EmbeddingProvider:
    return LocalSentenceTransformerProvider()


def get_llm_provider(workspace: Optional[Any] = None) -> LLMProvider:
    return _resolve_llm(workspace)


def get_embedding_provider(workspace: Optional[Any] = None) -> EmbeddingProvider:
    return _resolve_embedding(workspace)
