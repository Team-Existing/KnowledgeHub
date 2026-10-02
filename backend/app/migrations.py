"""
Security migrations, run on every startup after the schema (each is idempotent).

0. Usernames. Names are unique ignoring case. Users created before that get
   their `username_key` filled in; if two existing accounts differ only in
   case, the older keeps the plain key, the newer gets a suffixed one (it can
   still sign in with its exact name) and a warning names them so an admin can
   sort it out. Then the UNIQUE index on username_key is created.

1. Roles. Before role-based access control every account was created as an
   admin. The first time this runs on such a database, the earliest account
   keeps admin and every other admin becomes a member; admins can promote
   people again from the Users page. Recorded in Meta so it runs once and
   never touches roles an admin set later.

2. Connector addresses. Connectors no longer have built-in endpoints:
   GitHub connectors saved with "repo" (owner/name) + "api_url" get the
   equivalent "repo_url" link, and Linear connectors get the API address
   they were already using written into their config.

3. Credentials. Connector secrets stored in plaintext (written before
   encryption existed), or under a key that is no longer the current
   CREDENTIALS_KEY, are (re-)encrypted with the current key. Without a key
   nothing can be done: plaintext secrets are left unused (syncs refuse them)
   and an error is logged.
"""
from __future__ import annotations

import logging

from app import crypto
from app.arcadedb import ArcadeClient, ArcadeSession
from app.rbac import Role
from app import activity
from app.usernames import username_key
from app.services.connectors import REGISTRY
from app.services.connectors.sync import secret_context, secret_names

logger = logging.getLogger(__name__)


async def migrate_username_keys(session: ArcadeSession) -> list[str]:
    """Returns the usernames that clash with an older account (ignoring case)."""
    users = await session.query("SELECT id, username, username_key FROM User ORDER BY created_at, @rid")
    taken: dict[str, str] = {}
    clashes: list[str] = []
    for u in users:
        key = username_key(u["username"])
        if key in taken:
            clashes.append(f"{u['username']} (same as {taken[key]})")
            key = f"{key}~{u['id'][:8]}"
        else:
            taken[key] = u["username"]
        if u.get("username_key") != key:
            await session.execute("UPDATE User SET username_key = :k WHERE id = :id", {"k": key, "id": u["id"]})
    await session.commit()
    if clashes:
        logger.warning("Usernames that differ only in case from an older account: %s. They can still sign in "
                       "with their exact name; consider renaming them.", ", ".join(clashes))
    await session.client.command("CREATE INDEX IF NOT EXISTS ON User (username_key) UNIQUE")
    return clashes


async def start_activity_tracking(session: ArcadeSession) -> int:
    """
    Once, when activity tracking first runs: accounts and groups with no
    recorded activity get "now", so inactivity retention never deletes anything
    for time before tracking existed. Recorded in Meta so it never repeats
    (repeating would reset the clock of anyone who never signs in again).
    """
    if await session.query_one("SELECT key FROM Meta WHERE key = 'activity_tracking_since'"):
        return 0
    stamp = activity.now().isoformat()
    users = await session.execute("UPDATE User SET last_active_at = :t WHERE last_active_at IS NULL", {"t": stamp})
    groups = await session.execute("UPDATE GroupSpace SET last_active_at = :t WHERE last_active_at IS NULL", {"t": stamp})
    await session.execute("INSERT INTO Meta CONTENT :doc", {"doc": {"key": "activity_tracking_since", "value": {"at": stamp}}})
    await session.commit()
    count = sum(int(r[0].get("count", 0)) if r else 0 for r in (users, groups))
    logger.info("Activity tracking started: %d existing accounts/groups start their inactivity clock now", count)
    return count


async def migrate_roles(session: ArcadeSession) -> list[str]:
    """Returns the usernames that were demoted."""
    if await session.query_one("SELECT key FROM Meta WHERE key = 'rbac_v1'"):
        return []
    users = await session.query("SELECT id, username, role, created_at FROM User ORDER BY created_at, @rid")
    demoted: list[str] = []
    if users and not await session.query_one("SELECT key FROM Meta WHERE key = 'first_admin'"):
        first = users[0]
        await session.execute("UPDATE User SET role = :r WHERE id = :id", {"r": Role.ADMIN.value, "id": first["id"]})
        await session.execute("INSERT INTO Meta CONTENT :doc",
                              {"doc": {"key": "first_admin", "value": {"user_id": first["id"], "migrated": True}}})
        for u in users[1:]:
            if u.get("role") == Role.ADMIN.value:
                await session.execute("UPDATE User SET role = :r WHERE id = :id",
                                      {"r": Role.MEMBER.value, "id": u["id"]})
                demoted.append(u["username"])
        if demoted:
            logger.warning("RBAC migration: '%s' stays admin; demoted to member: %s",
                           first["username"], ", ".join(demoted))
    await session.execute("INSERT INTO Meta CONTENT :doc", {"doc": {"key": "rbac_v1", "value": {"done": True}}})
    await session.commit()
    return demoted


async def migrate_connector_addresses(session: ArcadeSession) -> int:
    changed = 0
    for c in await session.query("SELECT id, kind, config FROM Connector WHERE kind IN ['github', 'linear']"):
        config = dict(c.get("config") or {})
        if c["kind"] == "github" and config.get("repo") and not config.get("repo_url"):
            api = (config.get("api_url") or "https://api.github.com").rstrip("/")
            web = "https://github.com" if api == "https://api.github.com" else api.removesuffix("/api/v3")
            config["repo_url"] = f"{web}/{config.pop('repo').strip('/')}"
            if api == "https://api.github.com" or api.endswith("/api/v3"):
                config.pop("api_url", None)    # derivable from the link
        elif c["kind"] == "linear" and not config.get("api_url"):
            config["api_url"] = "https://api.linear.app/graphql"   # the endpoint it was hard-wired to
        else:
            continue
        await session.execute("UPDATE Connector SET config = :c WHERE id = :id", {"c": config, "id": c["id"]})
        changed += 1
    await session.commit()
    if changed:
        logger.info("Moved %d connector(s) to explicit, user-visible addresses", changed)
    return changed


async def migrate_credentials(session: ArcadeSession) -> int:
    """Returns how many stored secrets were (re-)encrypted."""
    changed, stuck = 0, 0
    for connector in await session.query("SELECT id, kind, config FROM Connector"):
        module = REGISTRY.get(connector["kind"])
        if not module:
            continue
        config = dict(connector.get("config") or {})
        context = secret_context(connector["id"])
        dirty = False
        for name in secret_names(module):
            value = config.get(name)
            if not value or not crypto.needs_reencryption(value):
                continue
            if not crypto.available():
                stuck += 1
                continue
            try:
                plain = crypto.decrypt(value, f"{context}:{name}") if crypto.is_encrypted(value) else value
            except crypto.DecryptionError as exc:
                logger.error("Connector %s: can't re-encrypt %s (%s); re-enter it on the connector",
                             connector["id"], name, exc)
                continue
            config[name] = crypto.encrypt(plain, f"{context}:{name}")
            dirty = True
            changed += 1
        if dirty:
            await session.execute("UPDATE Connector SET config = :c WHERE id = :id",
                                  {"c": config, "id": connector["id"]})
    await session.commit()
    if stuck:
        logger.error("%d connector credential(s) are stored unencrypted and will not be used until "
                     "CREDENTIALS_KEY is set (generate one with: python -m app.crypto)", stuck)
    elif changed:
        logger.info("Encrypted %d connector credential(s) with the current CREDENTIALS_KEY", changed)
    return changed


async def run(client: ArcadeClient) -> None:
    session = ArcadeSession(client)
    try:
        await migrate_username_keys(session)
        await start_activity_tracking(session)
        await migrate_roles(session)
        await migrate_connector_addresses(session)
        await migrate_credentials(session)
    finally:
        await session.close()
