"""
The three local LLMs the app supports, all served by Ollama.

Users pick one of these. At least one must be installed before anyone can
log in; each user's choice is stored on their account and used for every
LLM call they make (see providers.use_model).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

import httpx

from app.services.providers import OLLAMA_BASE, OLLAMA_MODEL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogModel:
    id: str            # the Ollama tag
    name: str
    size: str          # download size
    ram_required: str
    min_ram_gb: int
    description: str


CATALOG: List[CatalogModel] = [
    CatalogModel("llama3.1:8b", "Llama 3.1 8B", "4.7 GB", "8 GB+", 8,
                 "Balanced default: good extraction and answers on most laptops."),
    CatalogModel("mistral:7b", "Mistral 7B", "4.1 GB", "8 GB+", 8,
                 "Smallest download; fastest on modest hardware."),
    CatalogModel("gpt-oss:20b", "GPT-OSS 20B", "13 GB", "16 GB+", 16,
                 "Highest quality; needs a well-equipped machine."),
]
CATALOG_IDS: List[str] = [m.id for m in CATALOG]


def is_catalog_model(model_id: Optional[str]) -> bool:
    return model_id in CATALOG_IDS


class OllamaUnavailable(Exception):
    pass


async def installed_models() -> Set[str]:
    """Catalog models present in Ollama. Raises OllamaUnavailable if Ollama can't be reached."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{OLLAMA_BASE}/api/tags")
            response.raise_for_status()
            names = {m.get("name", "") for m in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError) as exc:
        raise OllamaUnavailable(f"Ollama is not reachable at {OLLAMA_BASE}: {exc}") from exc
    return {model_id for model_id in CATALOG_IDS if model_id in names}


async def availability() -> Tuple[bool, Set[str]]:
    """(ollama_reachable, installed catalog models) — never raises."""
    try:
        return True, await installed_models()
    except OllamaUnavailable:
        return False, set()


def pick_default(installed: Set[str], preferred: Optional[str] = None) -> Optional[str]:
    """The model to use: the preferred one if installed, else OLLAMA_MODEL, else catalog order."""
    for candidate in (preferred, OLLAMA_MODEL, *CATALOG_IDS):
        if candidate in installed:
            return candidate
    return None


def describe(installed: Set[str], active: Optional[str] = None) -> List[Dict[str, Any]]:
    return [
        {**{k: v for k, v in asdict(m).items() if k not in ("ram_required", "min_ram_gb")},
         "ramRequired": m.ram_required, "minRamGb": m.min_ram_gb,
         "provider": "ollama", "installed": m.id in installed, "active": m.id == active}
        for m in CATALOG
    ]


def recommended_for(ram_gb: int) -> str:
    # GPT-OSS 20B *runs* in 16 GB, but its 13 GB leaves too little for the OS and the
    # app on a 16 GB machine, so only recommend it with real headroom
    return "gpt-oss:20b" if ram_gb >= 24 else "llama3.1:8b" if ram_gb >= 8 else "mistral:7b"


async def pull_events(model_id: str) -> AsyncIterator[Dict[str, Any]]:
    """Ollama's pull progress events for a catalog model ({status, total, completed} …)."""
    timeout = httpx.Timeout(connect=5, read=900, write=30, pool=5)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{OLLAMA_BASE}/api/pull", json={"name": model_id, "stream": True}) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue


async def delete_model(model_id: str) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(
            "DELETE", f"{OLLAMA_BASE}/api/delete",
            content=json.dumps({"name": model_id}), headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
