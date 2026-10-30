"""
Groups: shared spaces inside an organization.

Any member of an organization can create a group in it and becomes its admin.
A group's admin searches the organization's members and invites them; an
invited user accepts or declines. Active members share everything in the
group's space. GET /spaces lists the spaces a user can switch to (sent back
as the X-Space header): their personal space, each organization's, and their
groups.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app.auth import get_current_user
from app.db import User, get_session
from app.repositories import UserRepository
from app.repositories.groups import (
    ACTIVE, DECLINED, GROUP_ADMIN, GROUP_MEMBER, INVITED, LEFT, REMOVED, GroupRepository,
)
from app.repositories.organizations import OrganizationRepository
from app.services.connectors.sync import is_running
from app.spaces import PERSONAL_SPACE_NAME

router = APIRouter(tags=["groups"])

Row = Dict[str, Any]


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    org_id: str = Field(min_length=1, max_length=40)


class GroupRename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class InviteRequest(BaseModel):
    user_id: str = Field(min_length=1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _membership(session: ArcadeSession, group_id: str, user: User) -> Row:
    membership = await GroupRepository(session).membership(group_id, user.id)
    if not membership or membership.get("status") != ACTIVE:
        raise HTTPException(status_code=404, detail="Group not found")
    return membership


async def _admin_of(session: ArcadeSession, group_id: str, user: User) -> Row:
    membership = await _membership(session, group_id, user)
    if membership.get("role") != GROUP_ADMIN:
        raise HTTPException(status_code=403, detail="Only the group's admin can do this")
    return membership


# ── spaces ──────────────────────────────────────────────────────────────────

@router.get("/spaces")
async def list_spaces(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    """The personal space first, then each organization followed by the user's groups in it."""
    orgs = await OrganizationRepository(session).orgs_of(current_user.id)
    groups = await GroupRepository(session).groups_of(current_user.id, ACTIVE)
    spaces: List[Row] = [{"id": current_user.id, "kind": "personal", "name": PERSONAL_SPACE_NAME, "role": "owner"}]
    for org in orgs:
        spaces.append({"id": org["id"], "kind": "organization", "name": org["name"], "role": org["role"],
                       "member_count": org["member_count"]})
        spaces.extend({"id": g["id"], "kind": "group", "name": g["name"], "role": g["role"],
                       "member_count": g["member_count"], "org_id": org["id"], "org_name": org["name"]}
                      for g in groups if g.get("org_id") == org["id"])
    return spaces


# ── groups ──────────────────────────────────────────────────────────────────

@router.post("/groups", status_code=201)
async def create_group(
    body: GroupCreate,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    """Any member of the organization can create a group in it; the creator is its admin."""
    if not await OrganizationRepository(session).active_member(body.org_id, current_user.id):
        raise HTTPException(status_code=404, detail="Organization not found")
    group = {"id": f"grp_{uuid.uuid4().hex[:16]}", "name": body.name.strip(), "org_id": body.org_id,
             "created_by": current_user.id, "created_at": _now()}
    repo = GroupRepository(session)
    await repo.add(group)
    await repo.set_membership(group["id"], current_user.id, role=GROUP_ADMIN, status=ACTIVE,
                              invited_by=None, created_at=group["created_at"], responded_at=group["created_at"])
    await session.commit()
    return {**group, "role": GROUP_ADMIN, "member_count": 1}


@router.get("/groups")
async def my_groups(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    return await GroupRepository(session).groups_of(current_user.id, ACTIVE)


@router.patch("/groups/{group_id}")
async def rename_group(
    group_id: str,
    body: GroupRename,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    await _admin_of(session, group_id, current_user)
    await GroupRepository(session).rename(group_id, body.name.strip())
    await session.commit()
    return await GroupRepository(session).get(group_id)


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    """Admin only. Deletes the group and everything in its space, for every member."""
    await _admin_of(session, group_id, current_user)
    running = [c["id"] for c in await session.query("SELECT id FROM Connector WHERE user_id = :g", {"g": group_id})
               if await is_running(c["id"])]
    if running:
        raise HTTPException(status_code=409, detail="A connector in this group is syncing; try again when it finishes")
    await GroupRepository(session).delete_everything(group_id)
    await session.commit()
    return Response(status_code=204)


@router.get("/groups/{group_id}/members")
async def group_members(
    group_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    """Active members for everyone; the admin also sees pending and declined invitations."""
    membership = await _membership(session, group_id, current_user)
    statuses = [ACTIVE, INVITED, DECLINED] if membership["role"] == GROUP_ADMIN else [ACTIVE]
    return [{"user_id": m["user_id"], "username": m["username"], "role": m["role"], "status": m["status"],
             "invited_by": m["invited_by_username"], "since": m.get("responded_at") or m["created_at"]}
            for m in await GroupRepository(session).members(group_id, statuses)]


@router.get("/groups/{group_id}/candidates")
async def search_users(
    group_id: str,
    q: str = Query(min_length=1, max_length=60),
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    """Admin only: find members of the group's organization to invite, with their status in this group."""
    await _admin_of(session, group_id, current_user)
    repo = GroupRepository(session)
    group = await repo.get(group_id)
    if not group or not group.get("org_id"):
        return []
    out = []
    for u in await OrganizationRepository(session).search_members(group["org_id"], q.strip(), current_user.id):
        m = await repo.membership(group_id, u["id"])
        out.append({"id": u["id"], "username": u["username"], "status": m["status"] if m else None})
    return out


@router.post("/groups/{group_id}/invitations", status_code=201)
async def invite(
    group_id: str,
    body: InviteRequest,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    await _admin_of(session, group_id, current_user)
    repo = GroupRepository(session)
    group = await repo.get(group_id)
    invitee = await UserRepository(session).get(body.user_id)
    # only members of the group's organization can be invited; others look the same as unknown ids
    if not invitee or not group or not group.get("org_id")             or not await OrganizationRepository(session).active_member(group["org_id"], invitee["id"]):
        raise HTTPException(status_code=404, detail="No member of this organization has that id")
    existing = await repo.membership(group_id, invitee["id"])
    if existing and existing["status"] == ACTIVE:
        raise HTTPException(status_code=409, detail=f"{invitee['username']} is already a member")
    if existing and existing["status"] == INVITED:
        raise HTTPException(status_code=409, detail=f"{invitee['username']} has already been invited")
    # declined, left or removed users can be invited again
    await repo.set_membership(group_id, invitee["id"], role=GROUP_MEMBER, status=INVITED,
                              invited_by=current_user.id, created_at=_now(), responded_at=None)
    await session.commit()
    return {"user_id": invitee["id"], "username": invitee["username"], "status": INVITED}


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
async def remove_member(
    group_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    """Admin only: remove a member, or cancel a pending invitation. What they added stays in the group."""
    await _admin_of(session, group_id, current_user)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="The admin can't remove themselves; delete the group instead")
    repo = GroupRepository(session)
    m = await repo.membership(group_id, user_id)
    if not m or m["status"] not in (ACTIVE, INVITED):
        raise HTTPException(status_code=404, detail="Not a member or invitee of this group")
    await repo.set_membership(group_id, user_id, status=REMOVED, responded_at=_now())
    await session.commit()
    return Response(status_code=204)


@router.post("/groups/{group_id}/leave", status_code=204)
async def leave_group(
    group_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    membership = await _membership(session, group_id, current_user)
    if membership["role"] == GROUP_ADMIN:
        raise HTTPException(status_code=400, detail="The admin can't leave their own group; delete it instead")
    await GroupRepository(session).set_membership(group_id, current_user.id, status=LEFT, responded_at=_now())
    await session.commit()
    return Response(status_code=204)


# ── invitations (the invited user's side) ───────────────────────────────────

@router.get("/invitations")
async def my_invitations(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    return [{"group_id": g["id"], "group_name": g["name"], "invited_by": g["invited_by"],
             "invited_at": g["invited_at"], "member_count": g["member_count"]}
            for g in await GroupRepository(session).groups_of(current_user.id, INVITED)]


async def _respond(session: ArcadeSession, group_id: str, user: User, status: str) -> None:
    repo = GroupRepository(session)
    m = await repo.membership(group_id, user.id)
    if not m or m["status"] != INVITED:
        raise HTTPException(status_code=404, detail="No pending invitation to that group")
    await repo.set_membership(group_id, user.id, status=status, responded_at=_now())
    await session.commit()


@router.post("/invitations/{group_id}/accept")
async def accept_invitation(
    group_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    await _respond(session, group_id, current_user, ACTIVE)
    group = await GroupRepository(session).get(group_id)
    return {"id": group_id, "kind": "group", "name": group["name"], "role": GROUP_MEMBER}


@router.post("/invitations/{group_id}/decline", status_code=204)
async def decline_invitation(
    group_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    await _respond(session, group_id, current_user, DECLINED)
    return Response(status_code=204)
