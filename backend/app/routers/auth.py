from __future__ import annotations

import hmac
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator, model_validator

from app.arcadedb import ArcadeSession, DuplicateKeyError
from app.auth import (TokenResponse, burn_password_check, check_new_password, create_token, get_current_user,
                      hash_password, verify_password)
from app.db import User, get_session
from app.rbac import DEFAULT_ROLE, Permission, Role, has_permission
from app.repositories import UserRepository
from app.usernames import check_new_username, username_key
from app import activity, ratelimit
from app.services import llm_catalog, providers

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=1, max_length=256)
    # required for the first (admin) account when the server sets SETUP_TOKEN
    setup_token: str | None = Field(default=None, max_length=512)

    @field_validator("username")
    @classmethod
    def _valid_username(cls, value: str) -> str:
        return check_new_username(value)

    @model_validator(mode="after")
    def _valid_password(self) -> "RegisterRequest":
        check_new_password(self.password, self.username)
        return self


def registration_open() -> bool:
    """ALLOW_REGISTRATION=false closes self-sign-up; admins then create accounts (POST /admin/users)."""
    return os.getenv("ALLOW_REGISTRATION", "true").strip().lower() not in ("false", "0", "no", "off")


def setup_token() -> str:
    """
    SETUP_TOKEN guards the first account, which becomes the server admin: on a
    server reachable from the internet, whoever registered first would
    otherwise own it. Empty = no token needed (a hub only you can reach).
    """
    return os.getenv("SETUP_TOKEN", "").strip()


async def admin_claimed(session: ArcadeSession) -> bool:
    return bool(await session.query_one("SELECT key FROM Meta WHERE key = 'first_admin'"))


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

    # bcrypt takes about a quarter of a second of CPU: off the event loop, which
    # every other request on this instance is waiting on
    hashed = await run_in_threadpool(hash_password, password)

    async def insert(as_role: Role, claim_admin: bool) -> Dict[str, Any]:
        user = {"id": str(uuid.uuid4()), "username": username, "username_key": username_key(username),
                "hashed_password": hashed,
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


@router.get("/setup")
async def setup_status(session: ArcadeSession = Depends(get_session)) -> Dict[str, bool]:
    """What the sign-in page needs to offer: whether the first account needs the setup token."""
    claimed = await admin_claimed(session)
    return {
        "setup_token_required": bool(setup_token()) and not claimed,
        "registration_open": registration_open() or not claimed,
    }


@router.post("/register", status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, str]:
    key = ratelimit.register_key(request)
    await ratelimit.check((key, ratelimit.REGISTRATIONS_PER_ADDRESS))
    await ratelimit.hit(key, window=ratelimit.REGISTER_WINDOW)
    if not await admin_claimed(session):
        # the first account can always be created, otherwise nobody could ever administer
        # the hub; with SETUP_TOKEN set, only by someone who has the server's configuration
        expected = setup_token()
        if expected and not hmac.compare_digest((body.setup_token or "").strip().encode(), expected.encode()):
            raise HTTPException(status_code=403, detail="The first account is the server admin and needs the "
                                "setup token (SETUP_TOKEN in the server's configuration).")
    elif not registration_open():
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


@router.post("/logout", status_code=204)
async def logout(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> None:
    """Sign out: every token this account holds stops working, on every device."""
    await UserRepository(session).revoke_tokens(current_user.id)
    await session.commit()


@router.post("/token", response_model=TokenResponse)
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    session: ArcadeSession = Depends(get_session),
) -> TokenResponse:
    account_key, address_key = ratelimit.login_keys(request, username_key(form.username))
    # checked before the password, so throttled guesses don't even cost a bcrypt round
    await ratelimit.check((account_key, ratelimit.LOGIN_FAILURES_PER_ACCOUNT),
                          (address_key, ratelimit.LOGIN_FAILURES_PER_ADDRESS))
    users = UserRepository(session)
    user = await users.get_by_username(form.username)
    if not user:
        # same cost as a wrong password: the timing reveals nothing
        await run_in_threadpool(burn_password_check, form.password)
    if not user or not await run_in_threadpool(verify_password, form.password, user["hashed_password"]):
        await ratelimit.hit(account_key, address_key, window=ratelimit.LOGIN_WINDOW)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    await ratelimit.clear(account_key)

    # An LLM is required: an installed local model, or an API model.
    reachable, installed = await llm_catalog.availability()
    usable = llm_catalog.usable(installed)
    if not usable:
        if providers.OLLAMA_ENABLED and not reachable:
            raise HTTPException(status_code=503, detail="Ollama is not running. Start Ollama, then sign in again.")
        raise HTTPException(
            status_code=412,
            detail="No AI model is available. Download one of the supported models to sign in."
            if providers.OLLAMA_ENABLED else "No AI model is configured on this server. Ask its administrator.",
        )
    # keep the user's choice if it's still usable, otherwise switch them to one that is
    model = llm_catalog.pick_default(usable, user.get("llm_model"))
    if model != user.get("llm_model"):
        await users.set_llm_model(user["id"], model)
        await session.commit()
    await activity.touch_user(session.client, user, login=True)
    return TokenResponse(access_token=create_token(user["id"], user.get("token_version") or 0))
