"""User administration (admins only)."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.arcadedb import ArcadeSession
from app.db import User, get_session
from app.rbac import Permission, Role, require
from app.repositories import UserRepository
from app.routers.auth import create_user
from app import activity
from app.services import retention
from app.usernames import check_new_username

router = APIRouter(prefix="/admin", tags=["admin"])

admin_only = require(Permission.MANAGE_USERS)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=6)
    role: Role = Role.MEMBER

    @field_validator("username")
    @classmethod
    def _valid_username(cls, value: str) -> str:
        return check_new_username(value)


class RoleUpdate(BaseModel):
    role: Role


def _public(u: Dict[str, Any]) -> Dict[str, Any]:
    last = activity.last_activity(u)
    return {"id": u["id"], "username": u["username"], "role": u.get("role") or Role.MEMBER.value,
            "created_at": u.get("created_at"), "last_login_at": u.get("last_login_at"),
            "last_active_at": last.isoformat() if last else None, "deletes_on": activity.deletes_on(u)}


@router.get("/users")
async def list_users(
    current_user: User = Depends(admin_only),
    session: ArcadeSession = Depends(get_session),
) -> List[Dict[str, Any]]:
    return [_public(u) for u in await UserRepository(session).list()]


@router.post("/users", status_code=201)
async def add_user(
    body: CreateUserRequest,
    current_user: User = Depends(admin_only),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Create an account directly — the way in when ALLOW_REGISTRATION=false."""
    return _public(await create_user(session, body.username, body.password, body.role))


@router.get("/retention")
async def retention_status(
    current_user: User = Depends(admin_only),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Settings, the last run, what is due now, and what will be due within 30 days."""
    due = await retention.plan(session)
    soon = await retention.plan(session, horizon_days=30)
    due_ids = {u["id"] for u in due["users"]} | {g["id"] for g in due["groups"]}
    return {
        "enabled": retention.enabled(),
        "inactive_days": activity.inactive_days(),
        "check_hours": retention.check_interval().total_seconds() / 3600,
        "last_run": await retention.last_run(session),
        "due": due,
        "upcoming": {"users": [u for u in soon["users"] if u["id"] not in due_ids],
                     "groups": [g for g in soon["groups"] if g["id"] not in due_ids]},
    }


@router.post("/retention/run")
async def retention_run_now(
    current_user: User = Depends(admin_only),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    """Delete everything that is due now (the same job that runs on its own every RETENTION_CHECK_HOURS)."""
    return await retention.run(session)


@router.patch("/users/{user_id}")
async def change_role(
    user_id: str,
    body: RoleUpdate,
    current_user: User = Depends(admin_only),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    users = UserRepository(session)
    target = await users.get(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == Role.ADMIN.value and body.role != Role.ADMIN \
            and await users.count_with_role(Role.ADMIN.value) <= 1:
        raise HTTPException(status_code=409, detail="This is the last admin. Promote someone else first.")
    await users.set_role(user_id, body.role.value)
    await session.commit()
    return _public({**target, "role": body.role.value})
