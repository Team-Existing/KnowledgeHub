from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List


def _coerce_string(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value)


def _coerce_tags(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _safe_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(payload.get("metadata") or {})
    metadata.setdefault("format", "okf")
    metadata.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    return metadata


def normalize_okf_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a loosely structured OKF-like payload into a convenient import shape."""
    if not isinstance(payload, dict):
        raise ValueError("OKF payload must be a JSON object")

    nodes = payload.get("nodes") or payload.get("entities") or payload.get("items") or []
    edges = payload.get("edges") or payload.get("relationships") or []

    normalized_nodes: List[Dict[str, Any]] = []
    for index, node in enumerate(nodes if isinstance(nodes, list) else []):
        if not isinstance(node, dict):
            continue
        node_id = _coerce_string(node.get("id") or node.get("uid") or node.get("uri") or node.get("key"), f"okf-node-{index + 1}")
        title = _coerce_string(node.get("name") or node.get("title") or node.get("label") or node.get("display_name"), f"Imported item {index + 1}")
        kind = _coerce_string(node.get("kind") or node.get("type") or node.get("category") or "knowledge-item")
        details = dict(node)
        details.pop("id", None)
        details.pop("uid", None)
        details.pop("uri", None)
        details.pop("key", None)
        normalized_nodes.append({
            "id": node_id,
            "title": title,
            "type": kind,
            "details": details,
            "tags": _coerce_tags(node.get("tags") or node.get("categories") or []),
            "source": _coerce_string(node.get("source") or payload.get("source") or "okf"),
            "author": _coerce_string(node.get("author") or payload.get("author") or "okf-import"),
        })

    normalized_edges: List[Dict[str, Any]] = []
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict):
            continue
        source = _coerce_string(edge.get("source") or edge.get("from") or edge.get("source_id") or edge.get("src"))
        target = _coerce_string(edge.get("target") or edge.get("to") or edge.get("target_id") or edge.get("dst"))
        if not source or not target:
            continue
        normalized_edges.append({
            "source": source,
            "target": target,
            "type": _coerce_string(edge.get("type") or edge.get("relation") or edge.get("label") or "RELATED_TO"),
            "details": dict(edge.get("metadata") or edge.get("details") or {}),
        })

    title = _coerce_string(payload.get("title") or payload.get("name") or payload.get("document_title"), "Imported OKF knowledge")
    content = payload.get("content")
    if not content:
        content = json.dumps(payload, indent=2)
    if not isinstance(content, str):
        content = json.dumps(content, indent=2)

    return {
        "title": title,
        "content": content,
        "source": "okf",
        "source_type": "okf",
        "author": _coerce_string(payload.get("author") or "okf-import"),
        "tags": _coerce_tags(payload.get("tags") or payload.get("categories") or []),
        "metadata": _safe_metadata(payload),
        "items": normalized_nodes,
        "relationships": normalized_edges,
    }


def export_okf_payload(workspace_id: str, artifacts: List[Dict[str, Any]], knowledge_items: List[Dict[str, Any]], relationships: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Serialize workspace knowledge into an OKF-like JSON payload."""
    nodes: List[Dict[str, Any]] = []
    for item in knowledge_items:
        node = {
            "id": item.get("id"),
            "name": item.get("title"),
            "kind": item.get("type"),
            "type": item.get("type"),
            "summary": item.get("details", {}).get("summary") or item.get("details", {}).get("content") or item.get("title"),
            "tags": item.get("tags", []),
            "metadata": {
                "artifact_id": item.get("artifact_id"),
                "review_status": item.get("review_status"),
                "details": item.get("details", {}),
            },
        }
        nodes.append(node)

    edges: List[Dict[str, Any]] = []
    for rel in relationships:
        edges.append({
            "source": rel.get("from"),
            "target": rel.get("to"),
            "type": rel.get("type"),
            "metadata": {},
        })

    return {
        "format": "okf",
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace_id": workspace_id,
        "title": f"Knowledge export for {workspace_id}",
        "content": f"Exported {len(artifacts)} artifacts and {len(knowledge_items)} knowledge items from Knowledge Hubs.",
        "nodes": nodes,
        "edges": edges,
        "relationships": edges,
        "entities": nodes,
        "artifacts": [
            {
                "id": artifact.get("id"),
                "title": artifact.get("title"),
                "source": artifact.get("source"),
                "source_type": artifact.get("source_type"),
                "author": artifact.get("author"),
                "tags": artifact.get("tags", []),
                "created_at": artifact.get("created_at"),
            }
            for artifact in artifacts
        ],
        "metadata": {
            "artifact_count": len(artifacts),
            "knowledge_item_count": len(knowledge_items),
            "relationship_count": len(relationships),
        },
    }
