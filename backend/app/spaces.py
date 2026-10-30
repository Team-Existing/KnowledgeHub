"""
Spaces: where a request's data lives.

Every user has a personal space (its id is the user's id), a space for each
organization they belong to (its id is the organization's), and one for each
group they are an active member of (groups live inside organizations). The frontend sends the active one
in the `X-Space` header; without it, requests use the personal space. Data
routes scope every query to `space.id`, so switching spaces switches
everything: sources, knowledge, decisions, connectors, GraphRAG.

Inside an organization or a group, every active member can read and change
what's there. Who manages membership: an organization's owners and admins
(routers/organizations.py), a group's admin (routers/groups.py). Leaving an
organization also ends its group memberships, and a group's space is refused
to anyone no longer in its organization.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import Depends, Header, HTTPException

from app.arcadedb import ArcadeSession
from app.auth import get_current_user
from app.db import User, get_session
from app.repositories.groups import ACTIVE, GroupRepository
from app.repositories.organizations import ORG_PREFIX, OrganizationRepository
from app import activity

PERSONAL_SPACE_NAME = "My space"


@dataclass(frozen=True)
class Space:
    id: str                                  # owner id stored on the data (user id or group id)
    kind: Literal["personal", "organization", "group"]
    name: str
    role: str                                # "owner" of a personal space; owner / admin / member of an
                                             # organization; "admin" / "member" in a group
    user: User                               # who is acting

    @property
    def actor(self) -> str:
        return self.user.username


def personal_space(user: User) -> Space:
    return Space(id=user.id, kind="personal", name=PERSONAL_SPACE_NAME, role="owner", user=user)


async def resolve_space(session: ArcadeSession, user: User, space_id: Optional[str]) -> Space:
    if not space_id or space_id == user.id:
        return personal_space(user)
    orgs = OrganizationRepository(session)
    if space_id.startswith(ORG_PREFIX):
        member = await orgs.active_member(space_id, user.id)
        org = await orgs.get(space_id) if member else None
        if not org:
            raise HTTPException(status_code=403, detail="You are not a member of that organization")
        return Space(id=org["id"], kind="organization", name=org["name"], role=member["role"], user=user)
    groups = GroupRepository(session)
    membership = await groups.membership(space_id, user.id)
    group = await groups.get(space_id) if membership else None
    if (not group or membership.get("status") != ACTIVE
            or (group.get("org_id") and not await orgs.active_member(group["org_id"], user.id))):
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
