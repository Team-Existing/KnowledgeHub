"""
ArcadeDB schema. Applied idempotently on every startup (all DDL is IF NOT EXISTS).

Graph
-----
Node                      supertype: id (unique across all nodes), user_id
 ├─ Artifact              a source document; its summary + summary_embedding live here
 └─ KnowledgeItem         an extracted item; embedding for vector search
CONTAINS                  Artifact -> KnowledgeItem
RELATED_TO                KnowledgeItem -> KnowledgeItem (cross-links, with score)
LINK                      any Node -> Node, from OKF import (property `type` = relation label)
EVOLVES                   KnowledgeItem -> KnowledgeItem, newer -> older (property `kind`:
                          supersedes | reverses | amends | realizes | mitigates | learned_from | implements)
System                    a service, database, team … (not a Node: kept out of the knowledge graph)
AFFECTS                   KnowledgeItem -> System: the item concerns that system
DEPENDS_ON                System -> System: the first depends on the second

Documents
---------
User, Playbook, QueryLog, Meta
ItemEvent                 per-item history (reviewed, edited, linked, status_changed, health_flagged, ...)
Connector                 an external source (folder, git ADRs, GitHub, Jira, Linear) + sync state
SyncedDocument            one fetched source document -> the artifact it was ingested as
Organization              the tenant: its own shared space, members, groups and invite links
OrgMember                 user <-> organization: role owner | admin | member, status active | left | removed
OrgInvite                 an invite link to an organization (only the token's hash is stored)
OrgDomain                 an email domain whose verified SSO users join the organization
SlackIntegration          a Slack app's /decisions command for a space (only its signing secret, encrypted)
CopilotRun                one Decision Copilot analysis of a pull request or issue: posted, updated, skipped or failed
SsoIdentity               a provider account (provider + subject) that signs in as a user
GroupSpace                a group: a shared space inside an organization (org_id)
Membership                user <-> group: status invited | active | declined | left | removed

Spaces: the `user_id` on every vertex, edge and per-space document is the id
of the *space* that owns it — a user's personal space (whose id is the user's
id, so data from before groups existed is already personal), an organization
or a group.

Every vertex and edge carries user_id; every query filters on it.
tags_text / tags_lc are derived from tags on write: full-text indexes can't
cover LIST properties, and tag filters are case-insensitive.
"""
from __future__ import annotations

import logging
from typing import List

from app.arcadedb import ArcadeClient

logger = logging.getLogger(__name__)

_TYPES = """
CREATE DOCUMENT TYPE User IF NOT EXISTS
CREATE PROPERTY User.id IF NOT EXISTS STRING
CREATE PROPERTY User.username IF NOT EXISTS STRING
CREATE PROPERTY User.username_key IF NOT EXISTS STRING
CREATE PROPERTY User.last_active_at IF NOT EXISTS STRING
CREATE PROPERTY User.last_login_at IF NOT EXISTS STRING
CREATE PROPERTY User.hashed_password IF NOT EXISTS STRING
CREATE PROPERTY User.role IF NOT EXISTS STRING
CREATE PROPERTY User.created_at IF NOT EXISTS STRING
CREATE PROPERTY User.token_version IF NOT EXISTS INTEGER
CREATE PROPERTY User.llm_model IF NOT EXISTS STRING
CREATE PROPERTY User.email IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON User (email) NOTUNIQUE
CREATE INDEX IF NOT EXISTS ON User (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON User (username) UNIQUE

CREATE VERTEX TYPE Node IF NOT EXISTS
CREATE PROPERTY Node.id IF NOT EXISTS STRING
CREATE PROPERTY Node.user_id IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON Node (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON Node (user_id) NOTUNIQUE

CREATE VERTEX TYPE Artifact IF NOT EXISTS EXTENDS Node
CREATE PROPERTY Artifact.title IF NOT EXISTS STRING
CREATE PROPERTY Artifact.content IF NOT EXISTS STRING
CREATE PROPERTY Artifact.source IF NOT EXISTS STRING
CREATE PROPERTY Artifact.source_type IF NOT EXISTS STRING
CREATE PROPERTY Artifact.author IF NOT EXISTS STRING
CREATE PROPERTY Artifact.tags IF NOT EXISTS LIST OF STRING
CREATE PROPERTY Artifact.tags_text IF NOT EXISTS STRING
CREATE PROPERTY Artifact.tags_lc IF NOT EXISTS LIST OF STRING
CREATE PROPERTY Artifact.extraction_engine IF NOT EXISTS STRING
CREATE PROPERTY Artifact.created_at IF NOT EXISTS STRING
CREATE PROPERTY Artifact.metadata IF NOT EXISTS MAP
CREATE PROPERTY Artifact.summary IF NOT EXISTS STRING
CREATE PROPERTY Artifact.summary_embedding IF NOT EXISTS ARRAY_OF_FLOATS
CREATE PROPERTY Artifact.summary_embedding_provider IF NOT EXISTS STRING
CREATE PROPERTY Artifact.summary_embedding_dims IF NOT EXISTS INTEGER
CREATE INDEX IF NOT EXISTS ON Artifact (source_type) NOTUNIQUE
CREATE INDEX IF NOT EXISTS ON Artifact (title, author, tags_text) FULL_TEXT
CREATE INDEX IF NOT EXISTS ON Artifact (title, author, tags_text, content) FULL_TEXT

CREATE VERTEX TYPE KnowledgeItem IF NOT EXISTS EXTENDS Node
CREATE PROPERTY KnowledgeItem.artifact_id IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.title IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.type IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.author IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.date IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.tags IF NOT EXISTS LIST OF STRING
CREATE PROPERTY KnowledgeItem.tags_text IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.tags_lc IF NOT EXISTS LIST OF STRING
CREATE PROPERTY KnowledgeItem.details IF NOT EXISTS MAP
CREATE PROPERTY KnowledgeItem.extraction_engine IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.embedding IF NOT EXISTS ARRAY_OF_FLOATS
CREATE PROPERTY KnowledgeItem.embedding_provider IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.embedding_dims IF NOT EXISTS INTEGER
CREATE PROPERTY KnowledgeItem.review_status IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.review_note IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.status IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.declared_status IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.health_signals IF NOT EXISTS LIST
CREATE PROPERTY KnowledgeItem.review_due IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.verified_at IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.verified_by IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.owner IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.due_date IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.expected_outcome IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.outcome_check_by IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.outcome IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.outcome_note IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.outcome_at IF NOT EXISTS STRING
CREATE PROPERTY KnowledgeItem.outcome_by IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON KnowledgeItem (artifact_id) NOTUNIQUE
CREATE INDEX IF NOT EXISTS ON KnowledgeItem (review_status) NOTUNIQUE
CREATE INDEX IF NOT EXISTS ON KnowledgeItem (title, tags_text, type) FULL_TEXT

CREATE EDGE TYPE CONTAINS IF NOT EXISTS
CREATE PROPERTY CONTAINS.user_id IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON CONTAINS (user_id) NOTUNIQUE
CREATE EDGE TYPE RELATED_TO IF NOT EXISTS
CREATE PROPERTY RELATED_TO.user_id IF NOT EXISTS STRING
CREATE PROPERTY RELATED_TO.score IF NOT EXISTS DOUBLE
CREATE INDEX IF NOT EXISTS ON RELATED_TO (user_id) NOTUNIQUE
CREATE EDGE TYPE LINK IF NOT EXISTS
CREATE PROPERTY LINK.user_id IF NOT EXISTS STRING
CREATE PROPERTY LINK.type IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON LINK (user_id) NOTUNIQUE
CREATE VERTEX TYPE System IF NOT EXISTS
CREATE PROPERTY System.id IF NOT EXISTS STRING
CREATE PROPERTY System.user_id IF NOT EXISTS STRING
CREATE PROPERTY System.name IF NOT EXISTS STRING
CREATE PROPERTY System.kind IF NOT EXISTS STRING
CREATE PROPERTY System.description IF NOT EXISTS STRING
CREATE PROPERTY System.aliases IF NOT EXISTS LIST OF STRING
CREATE PROPERTY System.owner IF NOT EXISTS STRING
CREATE PROPERTY System.created_at IF NOT EXISTS STRING
CREATE PROPERTY System.created_by IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON System (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON System (user_id) NOTUNIQUE

CREATE EDGE TYPE AFFECTS IF NOT EXISTS
CREATE PROPERTY AFFECTS.user_id IF NOT EXISTS STRING
CREATE PROPERTY AFFECTS.origin IF NOT EXISTS STRING
CREATE PROPERTY AFFECTS.created_at IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON AFFECTS (user_id) NOTUNIQUE

CREATE EDGE TYPE DEPENDS_ON IF NOT EXISTS
CREATE PROPERTY DEPENDS_ON.user_id IF NOT EXISTS STRING
CREATE PROPERTY DEPENDS_ON.note IF NOT EXISTS STRING
CREATE PROPERTY DEPENDS_ON.created_at IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON DEPENDS_ON (user_id) NOTUNIQUE

CREATE EDGE TYPE EVOLVES IF NOT EXISTS
CREATE PROPERTY EVOLVES.user_id IF NOT EXISTS STRING
CREATE PROPERTY EVOLVES.kind IF NOT EXISTS STRING
CREATE PROPERTY EVOLVES.note IF NOT EXISTS STRING
CREATE PROPERTY EVOLVES.origin IF NOT EXISTS STRING
CREATE PROPERTY EVOLVES.created_at IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON EVOLVES (user_id) NOTUNIQUE

CREATE DOCUMENT TYPE ItemEvent IF NOT EXISTS
CREATE PROPERTY ItemEvent.id IF NOT EXISTS STRING
CREATE PROPERTY ItemEvent.user_id IF NOT EXISTS STRING
CREATE PROPERTY ItemEvent.item_id IF NOT EXISTS STRING
CREATE PROPERTY ItemEvent.kind IF NOT EXISTS STRING
CREATE PROPERTY ItemEvent.at IF NOT EXISTS STRING
CREATE PROPERTY ItemEvent.detail IF NOT EXISTS MAP
CREATE PROPERTY ItemEvent.actor IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON ItemEvent (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON ItemEvent (item_id) NOTUNIQUE

CREATE DOCUMENT TYPE Connector IF NOT EXISTS
CREATE PROPERTY Connector.id IF NOT EXISTS STRING
CREATE PROPERTY Connector.user_id IF NOT EXISTS STRING
CREATE PROPERTY Connector.kind IF NOT EXISTS STRING
CREATE PROPERTY Connector.name IF NOT EXISTS STRING
CREATE PROPERTY Connector.config IF NOT EXISTS MAP
CREATE PROPERTY Connector.created_at IF NOT EXISTS STRING
CREATE PROPERTY Connector.last_sync_at IF NOT EXISTS STRING
CREATE PROPERTY Connector.last_status IF NOT EXISTS STRING
CREATE PROPERTY Connector.last_error IF NOT EXISTS STRING
CREATE PROPERTY Connector.last_result IF NOT EXISTS MAP
CREATE INDEX IF NOT EXISTS ON Connector (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON Connector (user_id) NOTUNIQUE

CREATE DOCUMENT TYPE GroupSpace IF NOT EXISTS
CREATE PROPERTY GroupSpace.id IF NOT EXISTS STRING
CREATE PROPERTY GroupSpace.name IF NOT EXISTS STRING
CREATE PROPERTY GroupSpace.created_by IF NOT EXISTS STRING
CREATE PROPERTY GroupSpace.created_at IF NOT EXISTS STRING
CREATE PROPERTY GroupSpace.last_active_at IF NOT EXISTS STRING
CREATE PROPERTY GroupSpace.org_id IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON GroupSpace (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON GroupSpace (org_id) NOTUNIQUE

CREATE DOCUMENT TYPE Organization IF NOT EXISTS
CREATE PROPERTY Organization.id IF NOT EXISTS STRING
CREATE PROPERTY Organization.name IF NOT EXISTS STRING
CREATE PROPERTY Organization.created_by IF NOT EXISTS STRING
CREATE PROPERTY Organization.created_at IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON Organization (id) UNIQUE

CREATE DOCUMENT TYPE OrgMember IF NOT EXISTS
CREATE PROPERTY OrgMember.id IF NOT EXISTS STRING
CREATE PROPERTY OrgMember.org_id IF NOT EXISTS STRING
CREATE PROPERTY OrgMember.user_id IF NOT EXISTS STRING
CREATE PROPERTY OrgMember.role IF NOT EXISTS STRING
CREATE PROPERTY OrgMember.status IF NOT EXISTS STRING
CREATE PROPERTY OrgMember.joined_at IF NOT EXISTS STRING
CREATE PROPERTY OrgMember.left_at IF NOT EXISTS STRING
CREATE PROPERTY OrgMember.invite_id IF NOT EXISTS STRING
CREATE PROPERTY OrgMember.updated_at IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON OrgMember (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON OrgMember (org_id) NOTUNIQUE
CREATE INDEX IF NOT EXISTS ON OrgMember (user_id) NOTUNIQUE

CREATE DOCUMENT TYPE SsoIdentity IF NOT EXISTS
CREATE PROPERTY SsoIdentity.id IF NOT EXISTS STRING
CREATE PROPERTY SsoIdentity.user_id IF NOT EXISTS STRING
CREATE PROPERTY SsoIdentity.provider IF NOT EXISTS STRING
CREATE PROPERTY SsoIdentity.subject IF NOT EXISTS STRING
CREATE PROPERTY SsoIdentity.email IF NOT EXISTS STRING
CREATE PROPERTY SsoIdentity.created_at IF NOT EXISTS STRING
CREATE PROPERTY SsoIdentity.last_used_at IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON SsoIdentity (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON SsoIdentity (user_id) NOTUNIQUE

CREATE DOCUMENT TYPE OrgDomain IF NOT EXISTS
CREATE PROPERTY OrgDomain.domain IF NOT EXISTS STRING
CREATE PROPERTY OrgDomain.org_id IF NOT EXISTS STRING
CREATE PROPERTY OrgDomain.added_by IF NOT EXISTS STRING
CREATE PROPERTY OrgDomain.added_at IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON OrgDomain (domain) UNIQUE
CREATE INDEX IF NOT EXISTS ON OrgDomain (org_id) NOTUNIQUE

CREATE DOCUMENT TYPE CopilotRun IF NOT EXISTS
CREATE PROPERTY CopilotRun.id IF NOT EXISTS STRING
CREATE PROPERTY CopilotRun.user_id IF NOT EXISTS STRING
CREATE PROPERTY CopilotRun.connector_id IF NOT EXISTS STRING
CREATE PROPERTY CopilotRun.kind IF NOT EXISTS STRING
CREATE PROPERTY CopilotRun.ref IF NOT EXISTS STRING
CREATE PROPERTY CopilotRun.revision IF NOT EXISTS STRING
CREATE PROPERTY CopilotRun.comment_id IF NOT EXISTS STRING
CREATE PROPERTY CopilotRun.status IF NOT EXISTS STRING
CREATE PROPERTY CopilotRun.created_at IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON CopilotRun (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON CopilotRun (user_id) NOTUNIQUE

CREATE DOCUMENT TYPE SlackIntegration IF NOT EXISTS
CREATE PROPERTY SlackIntegration.id IF NOT EXISTS STRING
CREATE PROPERTY SlackIntegration.user_id IF NOT EXISTS STRING
CREATE PROPERTY SlackIntegration.name IF NOT EXISTS STRING
CREATE PROPERTY SlackIntegration.signing_secret IF NOT EXISTS STRING
CREATE PROPERTY SlackIntegration.created_at IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON SlackIntegration (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON SlackIntegration (user_id) NOTUNIQUE

CREATE DOCUMENT TYPE OrgInvite IF NOT EXISTS
CREATE PROPERTY OrgInvite.id IF NOT EXISTS STRING
CREATE PROPERTY OrgInvite.org_id IF NOT EXISTS STRING
CREATE PROPERTY OrgInvite.token_hash IF NOT EXISTS STRING
CREATE PROPERTY OrgInvite.role IF NOT EXISTS STRING
CREATE PROPERTY OrgInvite.created_by IF NOT EXISTS STRING
CREATE PROPERTY OrgInvite.created_at IF NOT EXISTS STRING
CREATE PROPERTY OrgInvite.expires_at IF NOT EXISTS STRING
CREATE PROPERTY OrgInvite.max_uses IF NOT EXISTS INTEGER
CREATE PROPERTY OrgInvite.uses IF NOT EXISTS INTEGER
CREATE PROPERTY OrgInvite.revoked IF NOT EXISTS BOOLEAN
CREATE INDEX IF NOT EXISTS ON OrgInvite (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON OrgInvite (token_hash) UNIQUE
CREATE INDEX IF NOT EXISTS ON OrgInvite (org_id) NOTUNIQUE

CREATE DOCUMENT TYPE Membership IF NOT EXISTS
CREATE PROPERTY Membership.id IF NOT EXISTS STRING
CREATE PROPERTY Membership.group_id IF NOT EXISTS STRING
CREATE PROPERTY Membership.user_id IF NOT EXISTS STRING
CREATE PROPERTY Membership.role IF NOT EXISTS STRING
CREATE PROPERTY Membership.status IF NOT EXISTS STRING
CREATE PROPERTY Membership.invited_by IF NOT EXISTS STRING
CREATE PROPERTY Membership.created_at IF NOT EXISTS STRING
CREATE PROPERTY Membership.responded_at IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON Membership (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON Membership (group_id) NOTUNIQUE
CREATE INDEX IF NOT EXISTS ON Membership (user_id) NOTUNIQUE

CREATE DOCUMENT TYPE SyncedDocument IF NOT EXISTS
CREATE PROPERTY SyncedDocument.id IF NOT EXISTS STRING
CREATE PROPERTY SyncedDocument.user_id IF NOT EXISTS STRING
CREATE PROPERTY SyncedDocument.connector_id IF NOT EXISTS STRING
CREATE PROPERTY SyncedDocument.external_id IF NOT EXISTS STRING
CREATE PROPERTY SyncedDocument.artifact_id IF NOT EXISTS STRING
CREATE PROPERTY SyncedDocument.content_hash IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON SyncedDocument (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON SyncedDocument (connector_id) NOTUNIQUE

CREATE DOCUMENT TYPE Playbook IF NOT EXISTS
CREATE PROPERTY Playbook.id IF NOT EXISTS STRING
CREATE PROPERTY Playbook.user_id IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON Playbook (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON Playbook (user_id) NOTUNIQUE

CREATE DOCUMENT TYPE QueryLog IF NOT EXISTS
CREATE PROPERTY QueryLog.id IF NOT EXISTS STRING
CREATE PROPERTY QueryLog.user_id IF NOT EXISTS STRING
CREATE PROPERTY QueryLog.question IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON QueryLog (id) UNIQUE
CREATE INDEX IF NOT EXISTS ON QueryLog (user_id) NOTUNIQUE

CREATE DOCUMENT TYPE Meta IF NOT EXISTS
CREATE PROPERTY Meta.key IF NOT EXISTS STRING
CREATE INDEX IF NOT EXISTS ON Meta (key) UNIQUE
"""

# (type, property) pairs that get an LSM_VECTOR index sized to the embedding model
VECTOR_PROPERTIES = [("KnowledgeItem", "embedding"), ("Artifact", "summary_embedding")]


def _statements() -> List[str]:
    return [line.strip() for line in _TYPES.splitlines() if line.strip()]


async def apply_schema(client: ArcadeClient, embedding_dims: int, embedding_provider: str) -> None:
    for statement in _statements():
        await client.command(statement)
    await _ensure_vector_indexes(client, embedding_dims, embedding_provider)


async def _ensure_vector_indexes(client: ArcadeClient, dims: int, provider: str) -> None:
    """
    LSM_VECTOR indexes are fixed to one dimension and reject other sizes, and
    ArcadeDB doesn't report an index's dimension — so it's recorded in Meta.
    If the embedding model's dimension changed, the indexes are rebuilt and the
    stale vectors cleared; POST /knowledge/reembed then recomputes them.
    """
    rows = await client.command("SELECT value FROM Meta WHERE key = 'embeddings'")
    recorded = rows[0]["value"] if rows else None
    # provider of the vectors actually stored; only reembed_all() moves it forward
    vectors_provider = provider

    if recorded and recorded.get("dims") != dims:
        logger.warning(
            "Embedding dimension changed (%s -> %s): clearing stored vectors. "
            "Run POST /knowledge/reembed to rebuild them.", recorded.get("dims"), dims,
        )
        for type_name, prop in VECTOR_PROPERTIES:
            await client.command(f"DROP INDEX `{type_name}[{prop}]`")
            await client.command(f"UPDATE {type_name} SET {prop} = null")
    elif recorded and recorded.get("provider") != provider:
        logger.warning(
            "Embedding model changed (%s -> %s) with the same dimension: stored vectors "
            "are not comparable until you run POST /knowledge/reembed.", recorded.get("provider"), provider,
        )
        vectors_provider = recorded.get("provider")

    for type_name, prop in VECTOR_PROPERTIES:
        await client.command(
            f"CREATE INDEX IF NOT EXISTS ON {type_name} ({prop}) LSM_VECTOR "
            f'METADATA {{"dimensions": {int(dims)}, "similarity": "COSINE"}}'
        )
    await record_embedding_model(client, dims, vectors_provider)


async def record_embedding_model(client: ArcadeClient, dims: int, provider: str) -> None:
    await client.command(
        "UPDATE Meta SET key = 'embeddings', value = :value UPSERT WHERE key = 'embeddings'",
        {"value": {"dims": dims, "provider": provider}},
    )
