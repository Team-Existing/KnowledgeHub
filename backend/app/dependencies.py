"""Process-wide service singletons shared by routers and services."""
from __future__ import annotations

from app.services.curation_layer import CurationLayer
from app.services.ingestion_normalization import IngestionNormalization
from app.services.knowledge_extraction import KnowledgeExtraction

ingestion = IngestionNormalization()
extractor = KnowledgeExtraction()
curation = CurationLayer()
