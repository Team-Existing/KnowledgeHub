"""
Local LLM management. Only the three catalog models (services/llm_catalog.py)
can be listed, installed, removed or chosen; all are served by Ollama.

Public (used by the login screen before anyone is signed in):
  GET  /models/catalog            which of the three are installed, RAM, recommendation
  POST /models/bootstrap-install  download the first model — only while none is installed
Signed in:
  GET  /models/local, /models/status, /models/system-info
  POST /models/install, /models/remove, /models/set-default (= switch your active model)
"""
from __future__ import annotations

import json
import os
import platform
from typing import Any, AsyncIterator, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.arcadedb import ArcadeSession
from app.auth import get_current_user
from app.rbac import Permission, require
from app.db import User, get_session
from app.repositories import UserRepository
from app.services import llm_catalog
from app.services.providers import get_embedding_provider

router = APIRouter(prefix="/models", tags=["models"])


class ModelActionRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=120)
    model_config = ConfigDict(protected_namespaces=())


def _system_ram_gb() -> int:
    try:
        import psutil
        return max(1, round(psutil.virtual_memory().total / (1024 ** 3)))
    except Exception:
        # os.sysconf is available on Unix; use a safe useful fallback elsewhere.
        try:
            return max(1, round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3)))
        except Exception:
            return 8


def _require_catalog_model(model_id: str) -> None:
    if not llm_catalog.is_catalog_model(model_id):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model '{model_id}'. Choose one of: {', '.join(llm_catalog.CATALOG_IDS)}",
        )


async def _installed_or_503() -> set:
    try:
        return await llm_catalog.installed_models()
    except llm_catalog.OllamaUnavailable:
        raise HTTPException(status_code=503, detail="Ollama is not running. Start Ollama and try again.")


def _pull_stream(model_id: str) -> StreamingResponse:
    """Server-sent events with Ollama's download progress, ending in {'status': 'done'} or {'error'}."""
    async def events() -> AsyncIterator[str]:
        try:
            async for chunk in llm_catalog.pull_events(model_id):
                yield f"data: {json.dumps(chunk)}\n\n"
            installed = await llm_catalog.installed_models()
            if model_id not in installed:
                raise RuntimeError(f"{model_id} did not finish installing")
            yield f"data: {json.dumps({'status': 'done'})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Public: login screen
# ---------------------------------------------------------------------------

@router.get("/catalog")
async def model_catalog() -> Dict[str, Any]:
    """The three supported models and which are installed. Unauthenticated: the login screen needs it."""
    reachable, installed = await llm_catalog.availability()
    ram_gb = _system_ram_gb()
    return {
        "ollamaReachable": reachable,
        "anyInstalled": bool(installed),
        "ramGb": ram_gb,
        "recommended": llm_catalog.recommended_for(ram_gb),
        "models": llm_catalog.describe(installed),
    }


@router.post("/bootstrap-install")
async def bootstrap_install(body: ModelActionRequest) -> StreamingResponse:
    """
    Download the first model before anyone can sign in. Unauthenticated, so it
    only works while none of the three is installed; after that, installs go
    through the signed-in POST /models/install.
    """
    _require_catalog_model(body.model_id)
    if await _installed_or_503():
        raise HTTPException(status_code=409, detail="A model is already installed. Sign in to install more.")
    return _pull_stream(body.model_id)


# ---------------------------------------------------------------------------
# Signed in
# ---------------------------------------------------------------------------

@router.get("/system-info")
async def model_system_info(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    ram_gb = _system_ram_gb()
    return {"ramGb": ram_gb, "recommendedTier": llm_catalog.recommended_for(ram_gb), "platform": platform.system()}


@router.get("/local")
async def list_local_models(current_user: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    _, installed = await llm_catalog.availability()
    return llm_catalog.describe(installed, active=current_user.llm_model)


@router.get("/status")
async def model_status(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    reachable, installed = await llm_catalog.availability()
    embedding = get_embedding_provider()
    return {
        "llm": {"provider": "local", "model": current_user.llm_model,
                "installed": current_user.llm_model in installed, "ollamaReachable": reachable},
        "embedding": {"provider": "local", "installed": True,
                      "model": embedding.name.split(":", 1)[-1] if ":" in embedding.name else embedding.name},
    }


@router.post("/install")
async def install_local_model(
    body: ModelActionRequest, current_user: User = Depends(require(Permission.MANAGE_MODELS)),
) -> StreamingResponse:
    """Admins only: models live in the machine's one Ollama, shared by every account."""
    _require_catalog_model(body.model_id)
    await _installed_or_503()
    return _pull_stream(body.model_id)


@router.post("/remove")
async def remove_local_model(
    body: ModelActionRequest,
    current_user: User = Depends(require(Permission.MANAGE_MODELS)),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    _require_catalog_model(body.model_id)
    installed = await _installed_or_503()
    if body.model_id not in installed:
        raise HTTPException(status_code=404, detail=f"{body.model_id} is not installed")
    remaining = installed - {body.model_id}
    if not remaining:
        raise HTTPException(status_code=409, detail="This is the only installed model. Install another before removing it.")
    try:
        await llm_catalog.delete_model(body.model_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not remove the local model: {exc}")

    # Ollama is shared by every account on this machine: move anyone using it to another model
    replacement = llm_catalog.pick_default(remaining)
    users = UserRepository(session)
    for row in await users.using_model(body.model_id):
        await users.set_llm_model(row["id"], replacement)
    await session.commit()
    active = replacement if current_user.llm_model == body.model_id else current_user.llm_model
    return {"model_id": body.model_id, "removed": True, "active": active}


@router.post("/set-default")
async def set_active_model(
    body: ModelActionRequest,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Switch the signed-in user's model. Stored on the account, so it applies to future sessions too."""
    _require_catalog_model(body.model_id)
    if body.model_id not in await _installed_or_503():
        raise HTTPException(status_code=400, detail="Download this model before switching to it")
    await UserRepository(session).set_llm_model(current_user.id, body.model_id)
    await session.commit()
    return {"model_id": body.model_id, "active": body.model_id}
