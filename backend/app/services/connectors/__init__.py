"""
Connectors pull knowledge from where it already lives. Each module exposes
KIND, LABEL, FILESYSTEM (reads the server's disk → admins only), FIELDS
(config schema, also rendered by the frontend), `async fetch(config)` and,
for network connectors, `check(config)` (validates the user's addresses at save).

Network connectors have no built-in endpoints: they contact only the
addresses in their config, and base.make_client() refuses any other host.
"""
from __future__ import annotations

from types import ModuleType
from typing import Dict

from app.services.connectors import folder, git_adr, github, jira, linear

REGISTRY: Dict[str, ModuleType] = {m.KIND: m for m in (folder, git_adr, github, jira, linear)}
