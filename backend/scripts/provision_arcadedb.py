"""
One-off provisioning for a deployment (run before the app instances start):

  1. waits until the ArcadeDB cluster has elected a leader
  2. creates the database, as root
  3. creates the app's own ArcadeDB user, with access to that database only
     (it can read, write and change the schema there; it can't create
     databases or see any other one). Re-running it re-applies the password.
  4. applies the schema and the startup migrations, as that app user

The app instances then run with ARCADEDB_USER / ARCADEDB_PASSWORD set to the
app user and DB_MIGRATE_ON_STARTUP=false. Only this job ever sees the root
password.

    ARCADEDB_ROOT_PASSWORD=... python -m scripts.provision_arcadedb

Environment: ARCADEDB_URL (one or more nodes, comma-separated),
ARCADEDB_DATABASE, ARCADEDB_USER / ARCADEDB_PASSWORD (the app user to
create), ARCADEDB_ROOT_USER (default root) / ARCADEDB_ROOT_PASSWORD.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True) or find_dotenv())

from app.arcadedb import ArcadeClient, ArcadeDBError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s provision: %(message)s")
log = logging.getLogger("provision")
logging.getLogger("httpx").setLevel(logging.WARNING)   # one line per database call is noise here

WAIT_SECONDS = int(os.getenv("PROVISION_WAIT_SECONDS", "180"))


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        sys.exit(f"provision: {name} must be set")
    return value


async def _wait_for_leader(root: ArcadeClient) -> None:
    """
    Server commands (create database / user) need a leader. On a cold start every
    node answers "ready" a few seconds before the election ends, and some server
    commands (e.g. "list databases") work without a leader, so ask the cluster
    who leads and wait until it names someone.
    """
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            leader = await root.cluster_leader()
            reason = "no leader elected yet"
        except Exception as exc:   # nodes still starting
            leader, reason = None, str(exc)
        if leader:
            log.info("ArcadeDB ready (leader: %s)", leader)
            return
        if time.monotonic() > deadline:
            sys.exit(f"provision: ArcadeDB not ready after {WAIT_SECONDS}s ({reason})")
        log.info("waiting for ArcadeDB (%s)", reason[:120])
        await asyncio.sleep(3)


async def _ensure_app_user(root: ArcadeClient, name: str, password: str, database: str) -> None:
    spec = json.dumps({"name": name, "password": password, "databases": {database: "admin"}})
    try:
        await root.server_command(f"create user {spec}")
        log.info("created ArcadeDB user %s with access to %s only", name, database)
        return
    except ArcadeDBError as exc:
        if "already exist" not in (exc.detail or "").lower():
            raise
    # exists: recreate it so the password and database access match this deployment's config
    await root.server_command(f"drop user {name}")
    await root.server_command(f"create user {spec}")
    log.info("re-applied ArcadeDB user %s (access to %s only)", name, database)


async def main() -> None:
    url = _env("ARCADEDB_URL", "http://localhost:2480")
    database = _env("ARCADEDB_DATABASE", "knowledge_hubs")
    app_user, app_password = _env("ARCADEDB_USER"), _env("ARCADEDB_PASSWORD")
    root_user, root_password = _env("ARCADEDB_ROOT_USER", "root"), _env("ARCADEDB_ROOT_PASSWORD")
    if app_user == root_user:
        sys.exit("provision: ARCADEDB_USER must be a dedicated user, not the root user")
    if len(app_password) < 8:
        sys.exit("provision: ARCADEDB_PASSWORD must be at least 8 characters")

    root = ArcadeClient(url, database, root_user, root_password)
    try:
        await _wait_for_leader(root)
        await root.ensure_database()
        await _ensure_app_user(root, app_user, app_password, database)
    finally:
        await root.aclose()

    # schema + migrations as the app user, proving its access works
    from app import migrations
    from app.schema import apply_schema
    from app.services.providers import get_embedding_provider

    embedder = get_embedding_provider()
    app = ArcadeClient(url, database, app_user, app_password)
    try:
        await apply_schema(app, embedder.dimensions, embedder.name)
        await migrations.run(app)
    finally:
        await app.aclose()
    log.info("database %s is ready for the app", database)


if __name__ == "__main__":
    asyncio.run(main())
