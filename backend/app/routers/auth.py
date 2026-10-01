from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession, DuplicateKeyError
from app.auth import TokenResponse, create_token, hash_password, verify_password
from app.db import get_session
from app.repositories import UserRepository
from app.services import llm_catalog

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=6)


@router.post("/register", status_code=201)
async def register(
    body: RegisterRequest,
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, str]:
    users = UserRepository(session)
    if await users.get_by_username(body.username):
        raise HTTPException(status_code=409, detail="Username already taken")
    user_id = str(uuid.uuid4())
    try:
        await users.add({
            "id": user_id,
            "username": body.username,
            "hashed_password": hash_password(body.password),
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await session.commit()
    except DuplicateKeyError:   # lost a race with a concurrent registration
        raise HTTPException(status_code=409, detail="Username already taken")
    return {"user_id": user_id}


@router.post("/token", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: ArcadeSession = Depends(get_session),
) -> TokenResponse:
    users = UserRepository(session)
    user = await users.get_by_username(form.username)
    if not user or not verify_password(form.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # A local LLM is required: one of the three catalog models must be installed.
    reachable, installed = await llm_catalog.availability()
    if not reachable:
        raise HTTPException(status_code=503, detail="Ollama is not running. Start Ollama, then sign in again.")
    if not installed:
        raise HTTPException(
            status_code=412,
            detail="No local model is installed. Download one of the supported models to sign in.",
        )
    # keep the user's choice if it's still installed, otherwise switch them to one that is
    model = llm_catalog.pick_default(installed, user.get("llm_model"))
    if model != user.get("llm_model"):
        await users.set_llm_model(user["id"], model)
        await session.commit()
    return TokenResponse(access_token=create_token(user["id"]))
