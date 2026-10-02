"""
Role-based access control.

Roles map to permissions; routes declare the permission they need with
`Depends(require(Permission.X))` and never check role names themselves, so a
new role is one entry in ROLE_PERMISSIONS.

  admin   manages users, the machine's shared Ollama models, and connectors
          that read the server's disk
  member  everything on their own knowledge (each user's data is private)

Only the first account ever registered becomes an admin (see routers/auth.py);
everyone after that is a member until an admin promotes them.
"""
from __future__ import annotations

from enum import Enum
from typing import Callable, Dict, FrozenSet

from fastapi import Depends, HTTPException, status

from app.auth import get_current_user
from app.db import User


class Role(str, Enum):
    ADMIN = "admin"
    MEMBER = "member"


class Permission(str, Enum):
    MANAGE_USERS = "manage_users"
    MANAGE_MODELS = "manage_models"                    # install / remove models on the shared Ollama
    USE_FILESYSTEM_CONNECTORS = "use_filesystem_connectors"


ROLE_PERMISSIONS: Dict[Role, FrozenSet[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.MEMBER: frozenset(),
}

DEFAULT_ROLE = Role.MEMBER


def has_permission(user: User, permission: Permission) -> bool:
    try:
        role = Role(user.role)
    except ValueError:
        return False   # unknown role stored in the database: no privileges
    return permission in ROLE_PERMISSIONS[role]


def require(permission: Permission) -> Callable[..., User]:
    """Dependency: the signed-in user, or 403 if their role lacks `permission`."""
    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        if not has_permission(current_user, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Your role does not allow this; ask an administrator")
        return current_user
    return dependency
