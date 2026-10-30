"""
Organizations: the tenant.

Anyone signed in can create an organization and becomes its owner. Members
join through invite links. Each organization has its own shared space (its id
is the organization's) and the groups created in it.

Roles:
  owner   everything, including roles and deleting the organization; at least one always remains
  admin   rename it, create and revoke member invite links, remove members
  member  use the organization's space, create groups in it, leave

Email domains: an owner can claim their own verified email's domain (one that
an SSO provider vouched for, see services/sso.py). Anyone who then signs in
with SSO with a verified address at that domain joins as a member. Public mail
services (gmail.com …) can't be claimed, and each domain belongs to at most
one organization.

Invite links carry a random token; only its hash is stored, so a link is shown
once, when it's created. A link expires (1-30 days) and can be limited to a
number of uses. Someone removed from the organization can't rejoin through a
link created before they were removed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.arcadedb import ArcadeSession
from app.auth import get_current_user
from app.db import User, get_session
from app.repositories.groups import ACTIVE, REMOVED
from app.repositories.organizations import (
    ADMIN, LEFT, MEMBER, OWNER, OrganizationRepository, now, usable,
)
from app.repositories import UserRepository
from app.services import sso
from app.services.connectors.sync import is_running

router = APIRouter(prefix="/organizations", tags=["organizations"])

Row = Dict[str, Any]


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class OrgRename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class RoleChange(BaseModel):
    role: Literal["owner", "admin", "member"]


class DomainAdd(BaseModel):
    domain: str = Field(min_length=3, max_length=253)


class InviteCreate(BaseModel):
    role: Literal["admin", "member"] = MEMBER
    expires_in_days: int = Field(default=7, ge=1, le=30)
    max_uses: Optional[int] = Field(default=None, ge=1, le=1000)


async def _member(session: ArcadeSession, org_id: str, user: User) -> Row:
    member = await OrganizationRepository(session).active_member(org_id, user.id)
    if not member:
        # same answer whether it doesn't exist or you're not in it
        raise HTTPException(status_code=404, detail="Organization not found")
    return member


async def _at_least_admin(session: ArcadeSession, org_id: str, user: User) -> Row:
    member = await _member(session, org_id, user)
    if member["role"] not in (OWNER, ADMIN):
        raise HTTPException(status_code=403, detail="Only the organization's owners and admins can do this")
    return member


async def _owner(session: ArcadeSession, org_id: str, user: User) -> Row:
    member = await _member(session, org_id, user)
    if member["role"] != OWNER:
        raise HTTPException(status_code=403, detail="Only the organization's owners can do this")
    return member


async def _summary(repo: OrganizationRepository, org: Row, role: str) -> Row:
    return {"id": org["id"], "name": org["name"], "role": role, "created_at": org["created_at"],
            "member_count": await repo.count(org["id"])}


# ── organizations ───────────────────────────────────────────────────────────

@router.get("")
async def my_organizations(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    return [{"id": o["id"], "name": o["name"], "role": o["role"], "created_at": o["created_at"],
             "member_count": o["member_count"]}
            for o in await OrganizationRepository(session).orgs_of(current_user.id)]


@router.post("", status_code=201)
async def create_organization(
    body: OrgCreate,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    repo = OrganizationRepository(session)
    org = await repo.create(body.name.strip(), current_user.id)
    await session.commit()
    return await _summary(repo, org, OWNER)


@router.get("/{org_id}")
async def get_organization(
    org_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    member = await _member(session, org_id, current_user)
    repo = OrganizationRepository(session)
    return await _summary(repo, await repo.get(org_id), member["role"])


@router.patch("/{org_id}")
async def rename_organization(
    org_id: str,
    body: OrgRename,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    member = await _at_least_admin(session, org_id, current_user)
    repo = OrganizationRepository(session)
    await repo.rename(org_id, body.name.strip())
    await session.commit()
    return await _summary(repo, await repo.get(org_id), member["role"])


@router.delete("/{org_id}", status_code=204)
async def delete_organization(
    org_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    """Owners only. Deletes the organization, its groups and everything in their spaces."""
    await _owner(session, org_id, current_user)
    repo = OrganizationRepository(session)
    spaces = [org_id, *await repo.group_ids(org_id)]
    connectors = await session.query("SELECT id FROM Connector WHERE user_id IN :s", {"s": spaces})
    if any([await is_running(c["id"]) for c in connectors]):
        raise HTTPException(status_code=409, detail="A connector in this organization is syncing; try again when it finishes")
    await repo.delete_everything(org_id)
    await session.commit()
    return Response(status_code=204)


# ── members ─────────────────────────────────────────────────────────────────

@router.get("/{org_id}/members")
async def organization_members(
    org_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    await _member(session, org_id, current_user)
    return [{"user_id": m["user_id"], "username": m["username"], "role": m["role"], "joined_at": m["joined_at"]}
            for m in await OrganizationRepository(session).members(org_id)]


@router.patch("/{org_id}/members/{user_id}")
async def change_role(
    org_id: str,
    user_id: str,
    body: RoleChange,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    """Owners only. The last owner can't be demoted: make someone else an owner first."""
    await _owner(session, org_id, current_user)
    repo = OrganizationRepository(session)
    target = await repo.active_member(org_id, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Not a member of this organization")
    if target["role"] == OWNER and body.role != OWNER and await repo.count(org_id, OWNER) <= 1:
        raise HTTPException(status_code=409, detail="An organization needs an owner: make someone else an owner first")
    await repo.set_member(org_id, user_id, role=body.role)
    await session.commit()
    return {"user_id": user_id, "role": body.role}


@router.delete("/{org_id}/members/{user_id}", status_code=204)
async def remove_member(
    org_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    """
    Owners remove anyone but the last owner; admins remove members. The person
    also leaves every group in the organization. What they added stays.
    """
    me = await _at_least_admin(session, org_id, current_user)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="To remove yourself, leave the organization")
    repo = OrganizationRepository(session)
    target = await repo.active_member(org_id, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Not a member of this organization")
    if me["role"] == ADMIN and target["role"] != MEMBER:
        raise HTTPException(status_code=403, detail="Admins can only remove members; ask an owner")
    if target["role"] == OWNER and await repo.count(org_id, OWNER) <= 1:
        raise HTTPException(status_code=409, detail="An organization needs an owner")
    await repo.detach(org_id, user_id, REMOVED)
    await session.commit()
    return Response(status_code=204)


@router.post("/{org_id}/leave", status_code=204)
async def leave_organization(
    org_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    me = await _member(session, org_id, current_user)
    repo = OrganizationRepository(session)
    if me["role"] == OWNER and await repo.count(org_id, OWNER) <= 1:
        raise HTTPException(status_code=409, detail="You're the only owner: make someone else an owner, "
                                                    "or delete the organization")
    await repo.detach(org_id, current_user.id, LEFT)
    await session.commit()
    return Response(status_code=204)


# ── email domains ───────────────────────────────────────────────────────────

@router.get("/{org_id}/domains")
async def list_domains(
    org_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    await _at_least_admin(session, org_id, current_user)
    return await OrganizationRepository(session).domains(org_id)


@router.post("/{org_id}/domains", status_code=201)
async def add_domain(
    org_id: str,
    body: DomainAdd,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    """Owners only, and only the domain of their own email address as verified by an SSO provider."""
    await _owner(session, org_id, current_user)
    domain = body.domain.strip().lower().lstrip("@")
    user = await UserRepository(session).get(current_user.id)
    own = sso.email_domain(user.get("email"))
    if not own:
        raise HTTPException(status_code=403, detail="Sign in with SSO first: a domain can only be claimed by "
                                                    "someone whose email address there was verified")
    if domain != own:
        raise HTTPException(status_code=403, detail=f"You can only claim your own email's domain ({own})")
    if domain in sso.PUBLIC_EMAIL_DOMAINS:
        raise HTTPException(status_code=400, detail=f"{domain} is a public email service: anyone can have an "
                                                    "address there. Use invite links instead.")
    repo = OrganizationRepository(session)
    holder = await repo.domain_owner(domain)
    if holder:
        raise HTTPException(status_code=409, detail="This organization already has that domain" if holder == org_id
                            else "Another organization has already claimed that domain")
    await repo.add_domain(org_id, domain, current_user.id)
    await session.commit()
    return {"domain": domain}


@router.delete("/{org_id}/domains/{domain}", status_code=204)
async def remove_domain(
    org_id: str,
    domain: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    """Owners only. People who already joined through it stay."""
    await _owner(session, org_id, current_user)
    if not await OrganizationRepository(session).remove_domain(org_id, domain.lower()):
        raise HTTPException(status_code=404, detail="That domain isn't claimed by this organization")
    await session.commit()
    return Response(status_code=204)


# ── invite links ────────────────────────────────────────────────────────────

@router.get("/{org_id}/invites")
async def list_invites(
    org_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> List[Row]:
    await _at_least_admin(session, org_id, current_user)
    return await OrganizationRepository(session).invites(org_id)


@router.post("/{org_id}/invites", status_code=201)
async def create_invite(
    org_id: str,
    body: InviteCreate,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    """The response holds the token; it is never shown again (only its hash is stored)."""
    me = await _at_least_admin(session, org_id, current_user)
    if body.role == ADMIN and me["role"] != OWNER:
        raise HTTPException(status_code=403, detail="Only owners can invite admins")
    invite = await OrganizationRepository(session).create_invite(
        org_id, body.role, current_user.id, body.expires_in_days, body.max_uses)
    await session.commit()
    return {k: invite[k] for k in ("id", "token", "role", "created_at", "expires_at", "max_uses", "uses")}


@router.delete("/{org_id}/invites/{invite_id}", status_code=204)
async def revoke_invite(
    org_id: str,
    invite_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    await _at_least_admin(session, org_id, current_user)
    if not await OrganizationRepository(session).revoke_invite(org_id, invite_id):
        raise HTTPException(status_code=404, detail="Invite link not found")
    await session.commit()
    return Response(status_code=204)


# ── joining (the invited person's side) ─────────────────────────────────────

async def _valid_invite(repo: OrganizationRepository, token: str) -> Row:
    invite = await repo.invite_by_token(token)
    if not invite or not usable(invite):
        raise HTTPException(status_code=404, detail="This invite link is invalid or has expired. Ask for a new one.")
    return invite


@router.get("/join/{token}")
async def preview_invite(
    token: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    """What the link joins, so the person can decide. Signed in, so links can't be probed anonymously."""
    repo = OrganizationRepository(session)
    invite = await _valid_invite(repo, token)
    org = await repo.get(invite["org_id"])
    member = await repo.active_member(invite["org_id"], current_user.id)
    return {"org_id": org["id"], "org_name": org["name"], "role": invite["role"],
            "member_count": await repo.count(org["id"]), "already_member": bool(member)}


@router.post("/join/{token}")
async def join_organization(
    token: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Row:
    repo = OrganizationRepository(session)
    invite = await _valid_invite(repo, token)
    org_id = invite["org_id"]
    existing = await repo.member(org_id, current_user.id)
    if existing and existing.get("status") == ACTIVE:
        # already in: a link never changes someone's role, and doesn't use up a place
        return await _summary(repo, await repo.get(org_id), existing["role"])
    if existing and existing.get("status") == REMOVED and (existing.get("left_at") or "") > invite["created_at"]:
        raise HTTPException(status_code=403, detail="You were removed from this organization. "
                                                    "Ask an admin for a new invite link.")
    await repo.set_member(org_id, current_user.id, role=invite["role"], status=ACTIVE, joined_at=now(),
                          left_at=None, invite_id=invite["id"])
    await repo.use_invite(invite["id"])
    await session.commit()
    return await _summary(repo, await repo.get(org_id), invite["role"])
