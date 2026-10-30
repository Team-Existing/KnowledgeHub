"""Organizations (the tenant: members, roles, invite links) and their groups."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.arcadedb import ArcadeSession
from app.repositories.groups import ACTIVE, GROUP_ADMIN, REMOVED, GroupRepository

Row = Dict[str, Any]

# OrgMember.role, most powerful first
OWNER, ADMIN, MEMBER = "owner", "admin", "member"
ORG_ROLES = (OWNER, ADMIN, MEMBER)
# OrgMember.status (ACTIVE and REMOVED are shared with group memberships)
LEFT = "left"

ORG_PREFIX = "org_"


def org_member_id(org_id: str, user_id: str) -> str:
    return "om_" + hashlib.sha256(f"{org_id}:{user_id}".encode()).hexdigest()[:20]


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrganizationRepository:
    def __init__(self, session: ArcadeSession) -> None:
        self.session = session

    # ── organizations ───────────────────────────────────────────────────────

    async def get(self, org_id: str) -> Optional[Row]:
        return await self.session.query_one(
            "SELECT id, name, created_by, created_at FROM Organization WHERE id = :id", {"id": org_id})

    async def create(self, name: str, owner_id: str, org_id: Optional[str] = None) -> Row:
        org = {"id": org_id or f"{ORG_PREFIX}{uuid.uuid4().hex[:16]}", "name": name,
               "created_by": owner_id, "created_at": now()}
        await self.session.execute("INSERT INTO Organization CONTENT :doc", {"doc": org})
        await self.set_member(org["id"], owner_id, role=OWNER, status=ACTIVE, joined_at=org["created_at"])
        return org

    async def rename(self, org_id: str, name: str) -> None:
        await self.session.execute("UPDATE Organization SET name = :n WHERE id = :id", {"n": name, "id": org_id})

    async def group_ids(self, org_id: str) -> List[str]:
        return [r["id"] for r in await self.session.query(
            "SELECT id FROM GroupSpace WHERE org_id = :o", {"o": org_id})]

    async def delete_everything(self, org_id: str) -> None:
        """The organization, its groups, and everything in their spaces and its own."""
        groups = GroupRepository(self.session)
        for gid in await self.group_ids(org_id):
            await groups.delete_everything(gid)
        p = {"o": org_id}
        await self.session.execute("DELETE VERTEX FROM Node WHERE user_id = :o", p)   # edges go with them
        for doc in ("ItemEvent", "SyncedDocument", "Connector", "Playbook", "QueryLog"):
            await self.session.execute(f"DELETE FROM {doc} WHERE user_id = :o", p)
        await self.session.execute("DELETE FROM OrgInvite WHERE org_id = :o", p)
        await self.session.execute("DELETE FROM OrgDomain WHERE org_id = :o", p)
        await self.session.execute("DELETE FROM OrgMember WHERE org_id = :o", p)
        await self.session.execute("DELETE FROM Organization WHERE id = :o", p)

    # ── members ─────────────────────────────────────────────────────────────

    async def member(self, org_id: str, user_id: str) -> Optional[Row]:
        return await self.session.query_one(
            "SELECT FROM OrgMember WHERE id = :id", {"id": org_member_id(org_id, user_id)})

    async def active_member(self, org_id: str, user_id: str) -> Optional[Row]:
        m = await self.member(org_id, user_id)
        return m if m and m.get("status") == ACTIVE else None

    async def set_member(self, org_id: str, user_id: str, **fields: Any) -> None:
        mid = org_member_id(org_id, user_id)
        await self.session.execute(
            "UPDATE OrgMember MERGE :doc UPSERT WHERE id = :id",
            {"id": mid, "doc": {"id": mid, "org_id": org_id, "user_id": user_id, "updated_at": now(), **fields}},
        )

    async def members(self, org_id: str) -> List[Row]:
        rows = await self.session.query(
            "SELECT user_id, role, joined_at FROM OrgMember WHERE org_id = :o AND status = :s ORDER BY joined_at",
            {"o": org_id, "s": ACTIVE})
        names = await self._usernames([r["user_id"] for r in rows])
        return [{**r, "username": names.get(r["user_id"], "?")} for r in rows]

    async def count(self, org_id: str, role: Optional[str] = None) -> int:
        sql = "SELECT count(*) AS n FROM OrgMember WHERE org_id = :o AND status = :s"
        params: Row = {"o": org_id, "s": ACTIVE}
        if role:
            sql += " AND role = :r"
            params["r"] = role
        return await self.session.scalar(sql, params, 0)

    async def orgs_of(self, user_id: str) -> List[Row]:
        """The user's organizations, with their role and the member count."""
        rows = await self.session.query(
            "SELECT org_id, role, joined_at FROM OrgMember WHERE user_id = :u AND status = :s ORDER BY joined_at",
            {"u": user_id, "s": ACTIVE})
        out = []
        for r in rows:
            org = await self.get(r["org_id"])
            if org:
                out.append({**org, "role": r["role"], "member_count": await self.count(org["id"])})
        return out

    async def shares_org(self, user_a: str, user_b: str, org_id: str) -> bool:
        return bool(await self.active_member(org_id, user_a) and await self.active_member(org_id, user_b))

    async def search_members(self, org_id: str, text: str, exclude_user_id: str, limit: int = 20) -> List[Row]:
        ids = [r["user_id"] for r in await self.session.query(
            "SELECT user_id FROM OrgMember WHERE org_id = :o AND status = :s", {"o": org_id, "s": ACTIVE})]
        if not ids:
            return []
        return await self.session.query(
            "SELECT id, username FROM User WHERE id IN :ids AND username.toLowerCase() LIKE :q AND id <> :me "
            "ORDER BY username LIMIT :k",
            {"ids": ids, "q": f"%{text.lower()}%", "me": exclude_user_id, "k": limit})

    async def successor(self, org_id: str, leaving_user_id: str) -> Optional[str]:
        """Who becomes an owner when the last one goes: the longest-standing admin, else member."""
        for role in (ADMIN, MEMBER):
            row = await self.session.query_one(
                "SELECT user_id FROM OrgMember WHERE org_id = :o AND status = :s AND role = :r AND user_id <> :u "
                "ORDER BY joined_at LIMIT 1", {"o": org_id, "s": ACTIVE, "r": role, "u": leaving_user_id})
            if row:
                return row["user_id"]
        return None

    async def detach(self, org_id: str, user_id: str, status: str) -> None:
        """
        The user leaves the organization (status: left or removed), and with it every
        group in it. Groups they administered pass to their longest-standing remaining
        member, or, if nobody is left in one, to an owner, so the group's data stays
        with the organization.
        """
        groups = GroupRepository(self.session)
        stamp = now()
        for gid in await self.group_ids(org_id):
            m = await groups.membership(gid, user_id)
            if not m or m.get("status") not in (ACTIVE, "invited"):
                continue
            await groups.set_membership(gid, user_id, status=REMOVED, responded_at=stamp)
            if m.get("status") == ACTIVE and m.get("role") == GROUP_ADMIN:
                heir = await self.session.query_one(
                    "SELECT user_id FROM Membership WHERE group_id = :g AND status = 'active' "
                    "ORDER BY responded_at, created_at LIMIT 1", {"g": gid})
                if heir:
                    await groups.set_membership(gid, heir["user_id"], role=GROUP_ADMIN)
                else:
                    owner = await self.session.query_one(
                        "SELECT user_id FROM OrgMember WHERE org_id = :o AND status = :s AND role = :r "
                        "AND user_id <> :u ORDER BY joined_at LIMIT 1",
                        {"o": org_id, "s": ACTIVE, "r": OWNER, "u": user_id})
                    if owner:
                        await groups.set_membership(gid, owner["user_id"], role=GROUP_ADMIN, status=ACTIVE,
                                                    invited_by=None, created_at=stamp, responded_at=stamp)
        await self.set_member(org_id, user_id, status=status, left_at=stamp)

    # ── email domains (join on SSO sign-in) ─────────────────────────────────

    async def domains(self, org_id: str) -> List[Row]:
        rows = await self.session.query(
            "SELECT domain, added_by, added_at FROM OrgDomain WHERE org_id = :o ORDER BY domain", {"o": org_id})
        names = await self._usernames([r["added_by"] for r in rows])
        return [{**r, "added_by": names.get(r["added_by"], "?")} for r in rows]

    async def domain_owner(self, domain: str) -> Optional[str]:
        row = await self.session.query_one("SELECT org_id FROM OrgDomain WHERE domain = :d", {"d": domain})
        return row["org_id"] if row else None

    async def add_domain(self, org_id: str, domain: str, user_id: str) -> None:
        await self.session.execute("INSERT INTO OrgDomain CONTENT :doc", {"doc": {
            "domain": domain, "org_id": org_id, "added_by": user_id, "added_at": now()}})

    async def remove_domain(self, org_id: str, domain: str) -> bool:
        found = await self.session.query_one(
            "SELECT domain FROM OrgDomain WHERE domain = :d AND org_id = :o", {"d": domain, "o": org_id})
        if found:
            await self.session.execute("DELETE FROM OrgDomain WHERE domain = :d", {"d": domain})
        return bool(found)

    async def join_by_domain(self, user_id: str, domain: Optional[str]) -> Optional[str]:
        """
        Add the user to the organization that claimed their verified email's domain,
        as a member, unless they've been in it before (someone who left or was
        removed isn't pulled back in). Returns the organization's id if they joined.
        """
        org_id = await self.domain_owner(domain) if domain else None
        if not org_id or await self.member(org_id, user_id):
            return None
        await self.set_member(org_id, user_id, role=MEMBER, status=ACTIVE, joined_at=now(), via_domain=domain)
        return org_id

    # ── invite links ────────────────────────────────────────────────────────

    async def create_invite(self, org_id: str, role: str, created_by: str, expires_in_days: int,
                            max_uses: Optional[int]) -> Row:
        """A new invite link. The token is returned once; only its hash is stored."""
        token = secrets.token_urlsafe(32)
        created = datetime.now(timezone.utc)
        invite = {"id": f"inv_{uuid.uuid4().hex[:16]}", "org_id": org_id, "token_hash": token_hash(token),
                  "role": role, "created_by": created_by, "created_at": created.isoformat(),
                  "expires_at": (created + timedelta(days=expires_in_days)).isoformat(),
                  "max_uses": max_uses, "uses": 0, "revoked": False}
        await self.session.execute("INSERT INTO OrgInvite CONTENT :doc", {"doc": invite})
        return {**invite, "token": token}

    async def invites(self, org_id: str) -> List[Row]:
        rows = await self.session.query(
            "SELECT id, role, created_by, created_at, expires_at, max_uses, uses FROM OrgInvite "
            "WHERE org_id = :o AND revoked = false ORDER BY created_at DESC", {"o": org_id})
        names = await self._usernames([r["created_by"] for r in rows])
        return [{**r, "created_by": names.get(r["created_by"], "?"), "usable": usable(r)} for r in rows]

    async def invite_by_token(self, token: str) -> Optional[Row]:
        return await self.session.query_one(
            "SELECT FROM OrgInvite WHERE token_hash = :h AND revoked = false", {"h": token_hash(token)})

    async def use_invite(self, invite_id: str) -> None:
        await self.session.execute("UPDATE OrgInvite SET uses = uses + 1 WHERE id = :id", {"id": invite_id})

    async def revoke_invite(self, org_id: str, invite_id: str) -> bool:
        found = await self.session.query_one(
            "SELECT id FROM OrgInvite WHERE id = :id AND org_id = :o AND revoked = false", {"id": invite_id, "o": org_id})
        if found:
            await self.session.execute("UPDATE OrgInvite SET revoked = true WHERE id = :id", {"id": invite_id})
        return bool(found)

    async def _usernames(self, ids: List[str]) -> Dict[str, str]:
        ids = sorted({i for i in ids if i})
        if not ids:
            return {}
        rows = await self.session.query("SELECT id, username FROM User WHERE id IN :ids", {"ids": ids})
        return {r["id"]: r["username"] for r in rows}


def usable(invite: Row) -> bool:
    """Not revoked (callers filter that), not expired and not used up."""
    if invite.get("revoked"):
        return False
    if invite.get("expires_at", "") <= now():
        return False
    max_uses = invite.get("max_uses")
    return max_uses is None or (invite.get("uses") or 0) < max_uses
