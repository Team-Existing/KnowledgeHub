"""
Data-access layer over ArcadeDB. Repositories take an ArcadeSession and never
commit — the calling service or router owns the transaction.
"""
from app.repositories.artifacts import ArtifactRepository
from app.repositories.connectors import ConnectorRepository
from app.repositories.graph import GraphStore
from app.repositories.knowledge_items import KnowledgeItemRepository
from app.repositories.lineage import ItemEventRepository, LineageRepository
from app.repositories.misc import CrossLinkRepository, PlaybookRepository, QueryLogRepository
from app.repositories.relationships import RelationshipRepository
from app.repositories.search import SearchRepository
from app.repositories.users import UserRepository

__all__ = [
    "ArtifactRepository",
    "ConnectorRepository",
    "CrossLinkRepository",
    "GraphStore",
    "ItemEventRepository",
    "KnowledgeItemRepository",
    "LineageRepository",
    "PlaybookRepository",
    "QueryLogRepository",
    "RelationshipRepository",
    "SearchRepository",
    "UserRepository",
]
