"""
Connectors pull knowledge from where it already lives. Each module exposes
KIND, LABEL, FILESYSTEM (reads the server's disk → admins only), FIELDS
(config schema, also rendered by the frontend) and `async fetch(config)`.
"""
from __future__ import annotations

from types import ModuleType
from typing import Dict

from app.services.connectors import folder, git_adr, github, jira, linear

REGISTRY: Dict[str, ModuleType] = {m.KIND: m for m in (folder, git_adr, github, jira, linear)}
