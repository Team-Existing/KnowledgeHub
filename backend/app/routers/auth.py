from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, field_validator

from app.arcadedb import ArcadeSession, DuplicateKeyError
from app.auth import TokenResponse, create_token, get_current_user, hash_password, verify_password
from app.db import User, get_session
from app.rbac import DEFAULT_ROLE, Permission, Role, has_permission
from app.repositories import UserRepository
from app.usernames import check_new_username, username_key
from app import activity
from app.services import llm_catalog

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=6)

    @field_validator("username")
    @classmethod
    def _valid_username(cls, value: str) -> str:
        return check_new_username(value)


def registration_open() -> bool:
    """ALLOW_REGISTRATION=false closes self-sign-up; admins then create accounts (POST /admin/users)."""
    return os.getenv("ALLOW_REGISTRATION", "true").strip().lower() not in ("false", "0", "no", "off")


async def create_user(session: ArcadeSession, username: str, password: str, role: Role | None = None) -> Dict[str, Any]:
    """
    Create an account. With no explicit role, the very first account ever
    created becomes the admin and every later one a member. "First" is
    decided by inserting the unique Meta key 'first_admin' in the same
    transaction as the user, so concurrent sign-ups can't both become admin.

    Usernames are unique ignoring case (see app/usernames.py): the check here
    gives a clear 409, and the UNIQUE index on username_key catches races.
    """
    users = UserRepository(session)
    username = check_new_username(username)
    if await users.get_by_username(username):
        raise HTTPException(status_code=409, detail="Username already taken")

    async def insert(as_role: Role, claim_admin: bool) -> Dict[str, Any]:
        user = {"id": str(uuid.uuid4()), "username": username, "username_key": username_key(username),
                "hashed_password": hash_password(password),
                "role": as_role.value, "created_at": datetime.now(timezone.utc).isoformat()}
        if claim_admin:
            await session.execute("INSERT INTO Meta CONTENT :doc",
                                  {"doc": {"key": "first_admin", "value": {"user_id": user["id"]}}})
        await users.add(user)
        await session.commit()
        return user

    if role is None and not await session.query_one("SELECT key FROM Meta WHERE key = 'first_admin'"):
        try:
            return await insert(Role.ADMIN, claim_admin=True)
        except DuplicateKeyError:
            await session.rollback()   # someone else claimed admin first, or took this username
            if await users.get_by_username(username):
                raise HTTPException(status_code=409, detail="Username already taken")
    try:
        return await insert(role or DEFAULT_ROLE, claim_admin=False)
    except DuplicateKeyError:   # lost a race with a concurrent registration
        raise HTTPException(status_code=409, detail="Username already taken")


@router.post("/register", status_code=201)
async def register(
    body: RegisterRequest,
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, str]:
    # the first account can always be created, otherwise nobody could ever administer the hub
    if not registration_open() and await session.query_one("SELECT key FROM Meta WHERE key = 'first_admin'"):
        raise HTTPException(status_code=403, detail="Self-registration is closed. Ask an administrator for an account.")
    user = await create_user(session, body.username, body.password)
    return {"user_id": user["id"], "role": user["role"]}


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    return {
        "id": current_user.id,
        "username": current_user.username,
        "role": current_user.role,
        "permissions": sorted(p.value for p in Permission if has_permission(current_user, p)),
        "llm_model": current_user.llm_model,
    }


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
    await activity.touch_user(session.client, user, login=True)
    return TokenResponse(access_token=create_token(user["id"]))
