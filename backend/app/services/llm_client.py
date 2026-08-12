"""
LLM client shim — routes all calls through the active LLMProvider (Ollama).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.providers import get_llm_provider


async def chat(
    messages: List[Dict[str, str]],
    temperature: float = 0,
    max_tokens: int = 1000,
    json_mode: bool = False,
    workspace: Optional[Any] = None,
) -> Optional[str]:
    return await get_llm_provider(workspace).chat(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=json_mode,
    )
