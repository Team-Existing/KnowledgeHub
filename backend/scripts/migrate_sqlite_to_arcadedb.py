"""
One-off migration: copy a Knowledge Hubs SQLite database (the pre-ArcadeDB
storage) into ArcadeDB.

    cd backend
    python -m scripts.migrate_sqlite_to_arcadedb --sqlite data/knowledge.db

ArcadeDB connection settings come from ARCADEDB_URL / ARCADEDB_DATABASE /
ARCADEDB_USER / ARCADEDB_PASSWORD (backend/.env is loaded). The target
database must be empty; it is created and given the schema if missing. The
SQLite file is only read.

Mapping
  users                  -> User documents (password hashes kept, so logins still work)
  artifacts (+summaries) -> Artifact vertices (summary + summary_embedding folded in)
  knowledge_items        -> KnowledgeItem vertices (ids, review state, embeddings kept)
  relationships          -> CONTAINS edges (plus one for every item with an artifact_id);
                            other types -> LINK edges with the type as a label
  cross_links            -> RELATED_TO edges with their score
  playbooks, query_logs  -> Playbook / QueryLog documents
Edges whose endpoints are missing or belong to different users are skipped
and reported. Vectors whose length doesn't match the others are dropped
(re-create them with POST /knowledge/reembed).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from app.arcadedb import ArcadeClient, ArcadeSession  # noqa: E402
from app.repositories._common import tag_fields  # noqa: E402
from app.schema import apply_schema  # noqa: E402
from app.usernames import username_key  # noqa: E402

BATCH = 500


def _json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _rows(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not exists:
        return []
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]


def _chunks(seq: List[Any], size: int = BATCH) -> Iterable[List[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _vector_dims(items: List[Dict[str, Any]], summaries: List[Dict[str, Any]]) -> Counter:
    dims: Counter = Counter()
    for row in items + summaries:
        vec = _json(row.get("embedding"), None)
        if isinstance(vec, list) and vec:
            dims[len(vec)] += 1
    return dims


async def migrate(sqlite_path: str, client: ArcadeClient, embedding_dims: Optional[int]) -> Dict[str, Any]:
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    users = _rows(conn, "users")
    artifacts = _rows(conn, "artifacts")
    summaries = {s["artifact_id"]: s for s in _rows(conn, "artifact_summaries")}
    items = _rows(conn, "knowledge_items")
    relationships = _rows(conn, "relationships")
    cross_links = _rows(conn, "cross_links")
    playbooks = _rows(conn, "playbooks")
    query_logs = _rows(conn, "query_logs")

    found_dims = _vector_dims(items, list(summaries.values()))
    dims = embedding_dims or (found_dims.most_common(1)[0][0] if found_dims else 384)
    provider = next((i["embedding_provider"] for i in items if i.get("embedding_provider")), "migrated")
    report: Dict[str, Any] = {"embedding_dims": dims, "vectors_dropped": 0, "skipped_edges": Counter()}

    await client.ensure_database()
    await apply_schema(client, dims, provider)
    existing = (await client.command("SELECT count(*) AS n FROM Node"))[0]["n"] \
        + (await client.command("SELECT count(*) AS n FROM User"))[0]["n"]
    if existing:
        raise SystemExit(f"Target database '{client.database}' is not empty ({existing} users/nodes); "
                         "migrate into a new database.")

    def vector(value: Any) -> Optional[List[float]]:
        vec = _json(value, None)
        if isinstance(vec, list) and vec:
            if len(vec) == dims:
                return [float(x) for x in vec]
            report["vectors_dropped"] += 1
        return None

    session = ArcadeSession(client)

    async def insert_all(type_name: str, docs: List[Dict[str, Any]]) -> None:
        for chunk in _chunks(docs):
            await session.execute(f"INSERT INTO {type_name} CONTENT :docs", {"docs": chunk})
        await session.commit()

    await insert_all("User", [{
        "id": u["id"], "username": u["username"], "username_key": username_key(u["username"]),
        "hashed_password": u["hashed_password"],
        "role": u.get("role") or "member", "created_at": u.get("created_at"),
    } for u in users])

    artifact_owner = {a["id"]: a["user_id"] for a in artifacts}
    artifact_docs = []
    for a in artifacts:
        s = summaries.get(a["id"], {})
        artifact_docs.append({
            "id": a["id"], "user_id": a["user_id"], "title": a["title"], "content": a["content"],
            "source": a.get("source"), "source_type": a.get("source_type") or "manual",
            "author": a.get("author"), "extraction_engine": a.get("extraction_engine") or "regex",
            "created_at": a.get("created_at"), "metadata": _json(a.get("metadata"), {}),
            "summary": s.get("summary"), "summary_embedding": vector(s.get("embedding")),
            "summary_embedding_provider": s.get("embedding_provider"), "summary_embedding_dims": s.get("embedding_dims"),
            **tag_fields(_json(a.get("tags"), [])),
        })
    await insert_all("Artifact", artifact_docs)

    item_owner = {i["id"]: i["user_id"] for i in items}
    await insert_all("KnowledgeItem", [{
        "id": i["id"], "user_id": i["user_id"], "artifact_id": i.get("artifact_id"), "title": i["title"],
        "type": i["type"], "author": i.get("author"), "date": i.get("date"),
        "details": _json(i.get("details"), {}), "extraction_engine": i.get("extraction_engine") or "regex",
        "embedding": vector(i.get("embedding")), "embedding_provider": i.get("embedding_provider"),
        "embedding_dims": i.get("embedding_dims"),
        "review_status": i.get("review_status") or "pending", "review_note": i.get("review_note") or "",
        **tag_fields(_json(i.get("tags"), [])),
    } for i in items])

    owner = {**artifact_owner, **item_owner}

    def same_user(user_id: str, *node_ids: str) -> bool:
        return all(owner.get(n) == user_id for n in node_ids)

    # CONTAINS: from relationships, plus every item that names its artifact
    contains = {(r["from_id"], r["to_id"], r["user_id"]) for r in relationships if r["type"] == "CONTAINS"}
    contains |= {(i["artifact_id"], i["id"], i["user_id"]) for i in items if i.get("artifact_id")}
    scored = {(c["item_id_a"], c["item_id_b"], c["user_id"]): c["score"] for c in cross_links}
    related = dict(scored)
    for r in relationships:
        if r["type"] == "RELATED_TO":
            related.setdefault((r["from_id"], r["to_id"], r["user_id"]), None)
    links = {(r["from_id"], r["to_id"], r["user_id"], r["type"]) for r in relationships
             if r["type"] not in ("CONTAINS", "RELATED_TO")}

    created = Counter()
    for src, dst, uid in sorted(contains):
        if src in artifact_owner and dst in item_owner and same_user(uid, src, dst):
            await session.execute(
                "CREATE EDGE CONTAINS FROM (SELECT FROM Artifact WHERE id = :a) TO (SELECT FROM KnowledgeItem WHERE id = :b) "
                "IF NOT EXISTS SET user_id = :u", {"a": src, "b": dst, "u": uid})
            created["CONTAINS"] += 1
        else:
            report["skipped_edges"]["CONTAINS"] += 1
    for (src, dst, uid), score in sorted(related.items()):
        if src in item_owner and dst in item_owner and same_user(uid, src, dst):
            await session.execute(
                "CREATE EDGE RELATED_TO FROM (SELECT FROM KnowledgeItem WHERE id = :a) "
                "TO (SELECT FROM KnowledgeItem WHERE id = :b) SET user_id = :u, score = :s",
                {"a": src, "b": dst, "u": uid, "s": float(score) if score is not None else None})
            created["RELATED_TO"] += 1
        else:
            report["skipped_edges"]["RELATED_TO"] += 1
    for src, dst, uid, label in sorted(links):
        if src in owner and dst in owner and same_user(uid, src, dst):
            await session.execute(
                "CREATE EDGE LINK FROM (SELECT FROM Node WHERE id = :a) TO (SELECT FROM Node WHERE id = :b) "
                "IF NOT EXISTS SET user_id = :u, type = :t", {"a": src, "b": dst, "u": uid, "t": label})
            created["LINK"] += 1
        else:
            report["skipped_edges"]["LINK"] += 1
    await session.commit()

    await insert_all("Playbook", [{
        "id": p["id"], "user_id": p["user_id"], "title": p["title"],
        "steps": _json(p.get("steps"), []), "category": p.get("category"),
    } for p in playbooks])
    await insert_all("QueryLog", [{
        **{k: q.get(k) for k in ("id", "user_id", "question", "hyde_doc", "route", "retrieval_mode",
                                 "llm_provider", "embedding_provider", "answer_snippet", "latency_ms", "created_at")},
        "sub_queries": _json(q.get("sub_queries"), []),
        "context_node_ids": _json(q.get("context_node_ids"), []),
        "citations": _json(q.get("citations"), []),
    } for q in query_logs])

    # verify
    counts = {}
    for type_name, expected in (("User", len(users)), ("Artifact", len(artifacts)), ("KnowledgeItem", len(items)),
                                ("Playbook", len(playbooks)), ("QueryLog", len(query_logs)),
                                ("CONTAINS", created["CONTAINS"]), ("RELATED_TO", created["RELATED_TO"]),
                                ("LINK", created["LINK"])):
        actual = (await client.command(f"SELECT count(*) AS n FROM {type_name}"))[0]["n"]
        counts[type_name] = {"expected": expected, "actual": actual}
    report["counts"] = counts
    report["ok"] = all(c["expected"] == c["actual"] for c in counts.values())
    report["skipped_edges"] = dict(report["skipped_edges"])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", default="data/knowledge.db", help="source SQLite file (read-only)")
    parser.add_argument("--database", default=None, help="target ArcadeDB database (default: ARCADEDB_DATABASE)")
    parser.add_argument("--embedding-dims", type=int, default=None,
                        help="vector index size (default: the length of the stored vectors)")
    args = parser.parse_args()
    if not os.path.exists(args.sqlite):
        sys.exit(f"SQLite file not found: {args.sqlite}")

    async def run() -> Dict[str, Any]:
        client = ArcadeClient(
            url=os.getenv("ARCADEDB_URL", "http://localhost:2480"),
            database=args.database or os.getenv("ARCADEDB_DATABASE", "knowledge_hubs"),
            user=os.getenv("ARCADEDB_USER", "root"),
            password=os.getenv("ARCADEDB_PASSWORD", ""),
        )
        try:
            return await migrate(args.sqlite, client, args.embedding_dims)
        finally:
            await client.aclose()

    report = asyncio.run(run())
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        sys.exit("Row counts don't match — see report above.")


if __name__ == "__main__":
    main()
