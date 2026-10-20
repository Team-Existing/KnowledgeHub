"""
Quality and speed evaluation against a running Knowledge Hubs API and a real LLM.

    python -m evals.run_eval --base http://localhost:18000 --label baseline

Registers a fresh user, ingests the corpus (evals/corpus.py) through the same
endpoints the app uses, scores what was extracted against the answer key, then
asks every question and scores the answers. Writes evals/results/<label>.json
and prints a summary. Needs a real LLM: the numbers are what users would see.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from evals.corpus import QUESTIONS, REFUSAL_MARKERS, SOURCES

OUT = Path(__file__).resolve().parent / "results"


def _text(item: Dict[str, Any]) -> str:
    parts = [item.get("title") or ""]
    for v in (item.get("details") or {}).values():
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.extend(str(x) for x in v)
    return " ".join(parts).lower()


def _matches(text: str, groups: List[List[str]]) -> bool:
    return all(any(alt.lower() in text for alt in group) for group in groups)


def score_extraction(gold: List[Dict[str, Any]], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    used, found, found_any_type = set(), [], 0
    for g in gold:
        hit = next((i for i, it in enumerate(items)
                    if i not in used and it["type"] == g["type"] and _matches(_text(it), g["keywords"])), None)
        if hit is not None:
            used.add(hit)
            found.append(g)
        if any(_matches(_text(it), g["keywords"]) for it in items):
            found_any_type += 1
    # an extracted item is "useful" if it is about some gold item, whatever type it was given
    useful = sum(1 for it in items if any(_matches(_text(it), g["keywords"]) for g in gold))
    return {
        "gold": len(gold), "found": len(found), "found_any_type": found_any_type,
        "extracted": len(items), "useful": useful,
        "missed": [f"{g['type']}: {g['keywords']}" for g in gold if g not in found],
        "unmatched": [f"{it['type']}: {it['title']}" for it in items
                      if not any(_matches(_text(it), g["keywords"]) for g in gold)],
    }


def is_refusal(answer: str) -> bool:
    """As the app judges it: the fixed "not covered" sentence, or a short reply built on a refusal phrase.
    (A full answer that mentions in passing that something "is not specified" is not a refusal.)"""
    a = answer.strip().lower()
    return a.startswith("the knowledge base doesn't cover") or (
        len(a) < 400 and any(m in a for m in REFUSAL_MARKERS))


def wait_for_summaries(arcadedb: str, auth: str, user_id: str, expected: int, limit_s: int = 900) -> float:
    """The deferred summaries, read straight from ArcadeDB (no API exposes them)."""
    name, _, pw = auth.partition(":")
    db = httpx.Client(base_url=arcadedb.rstrip("/") + "/api/v1", auth=(name, pw), timeout=30)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < limit_s:
        r = db.post("/command/knowledge_hubs", json={"language": "sql", "params": {"u": user_id},
                    "command": "SELECT count(*) AS n FROM Artifact WHERE user_id = :u AND summary IS NOT NULL"})
        if r.json()["result"][0]["n"] >= expected:
            break
        time.sleep(3)
    return time.perf_counter() - t0


class _RetryingClient(httpx.Client):
    """Retries a request once when the connection itself fails (not on HTTP errors)."""

    def request(self, *args, **kwargs):
        try:
            return super().request(*args, **kwargs)
        except httpx.TransportError as exc:
            print(f"  (connection error, retrying once: {exc})", flush=True)
            time.sleep(5)
            return super().request(*args, **kwargs)


def run(base: str, label: str, arcadedb: Optional[str], arcadedb_auth: str) -> Dict[str, Any]:
    c = _RetryingClient(base_url=base, verify=False, timeout=900)
    user = f"eval_{uuid.uuid4().hex[:8]}"
    password = "eval-password-1"
    r = c.post("/auth/register", json={"username": user, "password": password})
    assert r.status_code == 201, r.text
    token = c.post("/auth/token", data={"username": user, "password": password}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    # ── extraction ──────────────────────────────────────────────────────────
    extraction, artifact_titles = [], {}
    for src in SOURCES:
        t0 = time.perf_counter()
        if src["kind"] in ("transcript", "email", "slack"):
            r = c.post("/knowledge/artifacts/transcript", headers=h, json={
                "title": src["title"], "content": src["content"], "source_type": src["kind"]})
        elif src["kind"] == "document":
            r = c.post("/knowledge/artifacts/upload", headers=h, data={"title": src["title"]},
                       files={"file": (src["title"][:40].replace(" ", "_").replace(":", "") + ".md",
                                       src["content"].encode(), "text/markdown")})
        else:
            r = c.post("/knowledge/artifacts", headers=h, json={"title": src["title"], "content": src["content"]})
        took = time.perf_counter() - t0
        assert r.status_code == 200, (src["title"], r.status_code, r.text[:300])
        body = r.json()
        artifact_titles[body["artifact"]["id"]] = src["title"]
        s = score_extraction(src["gold"], body["items"])
        s.update({"title": src["title"], "kind": src["kind"], "seconds": round(took, 1),
                  "llm_error": body.get("llm_error")})
        extraction.append(s)
        print(f"  ingest {src['kind']:10s} {took:6.1f}s  found {s['found']}/{s['gold']}  "
              f"useful {s['useful']}/{s['extracted']}  {src['title']}", flush=True)

    # wait for the deferred summaries: they're part of retrieval, and they mustn't
    # compete with the questions for the LLM while those are being timed
    if arcadedb:
        me = c.get("/auth/me", headers=h).json()["id"]
        waited = wait_for_summaries(arcadedb, arcadedb_auth, me, len(SOURCES))
        print(f"  summaries ready after {waited:.0f}s", flush=True)

    items = c.get("/knowledge/items", headers=h, params={"limit": 1000}).json()["items"]
    item_artifact = {i["id"]: i["artifact_id"] for i in items}

    # ── questions ───────────────────────────────────────────────────────────
    answers = []
    for q in QUESTIONS:
        t0 = time.perf_counter()
        r = c.post("/knowledge/graphrag/query", headers=h, json={"question": q["q"]})
        took = time.perf_counter() - t0
        assert r.status_code == 200, (q["q"], r.text[:300])
        body = r.json()
        answer = body["answer"]
        cited = {artifact_titles.get(item_artifact.get(cit["id"], cit["id"]), "?") for cit in body["citations"]}
        context_sources = {artifact_titles.get(item_artifact.get(n["id"], n["id"]), "?") for n in body["context_nodes"]}
        row: Dict[str, Any] = {"q": q["q"], "seconds": round(took, 1), "timings_ms": body.get("timings_ms"),
                               "route": body.get("route"), "answer": answer, "cited": sorted(cited),
                               "citations": len(body["citations"])}
        if q.get("answerable", True):
            a = answer.lower()
            facts_found = sum(any(alt in a for alt in group) for group in q["facts"])
            row.update({
                "answerable": True,
                "facts": f"{facts_found}/{len(q['facts'])}",
                "correct": facts_found == len(q["facts"]) and not is_refusal(answer),
                "refused": is_refusal(answer),
                "source_cited": bool(cited & set(q["sources"])),
                "source_retrieved": bool(context_sources & set(q["sources"])),
            })
        else:
            row.update({"answerable": False, "correct": is_refusal(answer) and not body["citations"],
                        "refused": is_refusal(answer)})
        answers.append(row)
        mark = "ok " if row["correct"] else "BAD"
        print(f"  {mark} {took:6.1f}s  {q['q'][:60]:60s} cited={row['citations']}", flush=True)

    # ── summary ─────────────────────────────────────────────────────────────
    llm_sources = [e for e in extraction if e["kind"] != "text"]
    answerable = [a for a in answers if a["answerable"]]
    unanswerable = [a for a in answers if not a["answerable"]]
    summary = {
        "label": label,
        "extraction_recall": round(sum(e["found"] for e in llm_sources) / sum(e["gold"] for e in llm_sources), 3),
        "extraction_recall_any_type": round(sum(e["found_any_type"] for e in llm_sources)
                                            / sum(e["gold"] for e in llm_sources), 3),
        "extraction_precision": round(sum(e["useful"] for e in llm_sources)
                                      / max(1, sum(e["extracted"] for e in llm_sources)), 3),
        "extraction_items": sum(e["extracted"] for e in llm_sources),
        "ingest_seconds_mean": round(statistics.mean(e["seconds"] for e in llm_sources), 1),
        "answers_correct": f"{sum(a['correct'] for a in answerable)}/{len(answerable)}",
        "answers_source_cited": f"{sum(a['source_cited'] for a in answerable)}/{len(answerable)}",
        "answers_source_retrieved": f"{sum(a['source_retrieved'] for a in answerable)}/{len(answerable)}",
        "false_refusals": sum(a["refused"] for a in answerable),
        "unanswerable_refused": f"{sum(a['correct'] for a in unanswerable)}/{len(unanswerable)}",
        "answer_seconds_median": round(statistics.median(a["seconds"] for a in answers), 1),
        "answer_seconds_p90": round(sorted(a["seconds"] for a in answers)[int(0.9 * (len(answers) - 1))], 1),
    }
    OUT.mkdir(exist_ok=True)
    (OUT / f"{label}.json").write_text(json.dumps(
        {"summary": summary, "extraction": extraction, "answers": answers}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:18000")
    ap.add_argument("--label", default="run")
    ap.add_argument("--arcadedb", help="e.g. http://localhost:12480, to wait for the deferred summaries")
    ap.add_argument("--arcadedb-auth", default="root:eval-password")
    args = ap.parse_args()
    run(args.base, args.label, args.arcadedb, args.arcadedb_auth)
