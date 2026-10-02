"""
Spaces: where a request's data lives.

Every user has a personal space (its id is the user's id) and a space for
each group they are an active member of. The frontend sends the active one
in the `X-Space` header; without it, requests use the personal space. Data
routes scope every query to `space.id`, so switching spaces switches
everything: sources, knowledge, decisions, connectors, GraphRAG.

Inside a group, every active member can read and change what's there; the
group's admin (its creator) additionally manages who is in it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import Depends, Header, HTTPException

from app.arcadedb import ArcadeSession
from app.auth import get_current_user
from app.db import User, get_session
from app.repositories.groups import ACTIVE, GroupRepository
from app import activity

PERSONAL_SPACE_NAME = "My space"


@dataclass(frozen=True)
class Space:
    id: str                                  # owner id stored on the data (user id or group id)
    kind: Literal["personal", "group"]
    name: str
    role: str                                # "owner" of a personal space; "admin" / "member" in a group
    user: User                               # who is acting

    @property
    def actor(self) -> str:
        return self.user.username


def personal_space(user: User) -> Space:
    return Space(id=user.id, kind="personal", name=PERSONAL_SPACE_NAME, role="owner", user=user)


async def resolve_space(session: ArcadeSession, user: User, space_id: Optional[str]) -> Space:
    if not space_id or space_id == user.id:
        return personal_space(user)
    groups = GroupRepository(session)
    membership = await groups.membership(space_id, user.id)
    group = await groups.get(space_id) if membership else None
    if not group or membership.get("status") != ACTIVE:
        # same answer whether the group doesn't exist or you're not in it
        raise HTTPException(status_code=403, detail="You are not a member of that group")
    await activity.touch_group(session.client, group)   # any member's work keeps the group alive
    return Space(id=group["id"], kind="group", name=group["name"], role=membership["role"], user=user)


async def current_space(
    x_space: Optional[str] = Header(default=None, alias="X-Space"),
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Space:
    return await resolve_space(session, current_user, x_space)
