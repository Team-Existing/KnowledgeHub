"""GraphRAG query pipeline with the LLM mocked (Neo4j disabled → SQLite fallback retrieval)."""
from __future__ import annotations

import json
import re

import pytest

from tests.helpers import ingest_text

TRANSFORM = json.dumps({
    "sub_queries": ["which database was chosen", "billing storage decision", "postgres adoption"],
    "hyde_doc": "The team decided to adopt PostgreSQL for billing.",
})


def context_ids(messages):
    """Item ids shown to the LLM in the KNOWLEDGE ITEMS section of the prompt."""
    context = messages[-1]["content"]
    section = context.split("=== KNOWLEDGE ITEMS ===", 1)[-1].split("===", 1)[0]
    return re.findall(r"^\[([^\]]+)\]", section, flags=re.M)


def cite_first(messages):
    ids = context_ids(messages)
    return f"The team chose PostgreSQL [{ids[0]}]." if ids else "Nothing found."


@pytest.fixture
def kb(client, alice):
    ingest_text(client, alice)
    ingest_text(client, alice, title="Release process",
                content="We agreed to release weekly on Mondays. Risk: on-call burnout for the release team.")
    return alice


def ask(client, headers, question="What did we decide about PostgreSQL?", **extra):
    r = client.post("/knowledge/graphrag/query", headers=headers, json={"question": question, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_answer_is_grounded_and_cited(client, kb, llm):
    llm.handlers["transform"] = lambda m: TRANSFORM
    llm.handlers["generate"] = cite_first
    body = ask(client, kb)

    assert body["retrieval_mode"] == "graph_vector_keyword"
    assert body["sub_queries"][0] == "What did we decide about PostgreSQL?"
    shown = context_ids(llm.last("generate"))
    assert shown, "generate prompt should list context items"
    # the most relevant item for the question is the PostgreSQL decision
    top = next(n for n in body["context_nodes"] if n["id"] == shown[0])
    assert "PostgreSQL" in top["title"]
    # citations carry the decision's lifecycle status, so the UI can flag superseded sources
    assert body["citations"] == [{"id": shown[0], "title": top["title"], "type": "decision", "status": "active"}]
    assert f"[{shown[0]}]" in body["answer"]


def test_hallucinated_citations_are_dropped(client, kb, llm):
    llm.handlers["generate"] = lambda m: "Per [decision_000000000000] and [Some Title], we chose Postgres."
    body = ask(client, kb)
    assert body["citations"] == []


def test_grouped_citations_are_all_resolved(client, kb, llm):
    llm.handlers["generate"] = lambda m: "Both apply [{}].".format(", ".join(context_ids(m)[:2]))
    body = ask(client, kb)
    assert len(body["citations"]) == 2


def test_pipeline_stages_and_timings(client, kb, llm):
    llm.handlers["transform"] = lambda m: TRANSFORM
    llm.handlers["generate"] = cite_first
    body = ask(client, kb)
    assert llm.kinds().count("transform") == 1
    assert llm.kinds().count("generate") == 1
    assert set(body["timings_ms"]) >= {"transform_route", "embed", "retrieve", "generate"}
    assert body["route"] in {"factual", "exploratory", "comparative", "procedural"}


def test_works_with_no_llm_reachable(client, kb, llm):
    body = ask(client, kb)            # every LLM call returns None
    assert body["context_nodes"]
    assert "PostgreSQL" in body["answer"]            # extractive fallback answer built from context
    assert body["sub_queries"] == ["What did we decide about PostgreSQL?"]
    assert body["citations"] == []


def test_malformed_transform_output_is_tolerated(client, kb, llm):
    llm.handlers["transform"] = lambda m: "Here are some rewrites: 1) ..."
    llm.handlers["generate"] = cite_first
    body = ask(client, kb)
    assert body["sub_queries"] == ["What did we decide about PostgreSQL?"]
    assert body["citations"]


def test_ambiguous_route_falls_back_to_llm_classifier(client, kb, llm, monkeypatch):
    import app.services.graphrag as graphrag

    async def unsure(q_vec):          # embedding router can't decide
        return None
    monkeypatch.setattr(graphrag, "_route_by_embedding", unsure)
    llm.handlers["route"] = lambda m: "comparative"
    assert ask(client, kb, question="postgres versus release cadence")["route"] == "comparative"
    assert llm.kinds().count("route") == 1

    llm.reset()
    llm.handlers["route"] = lambda m: "nonsense"   # invalid label → keyword router
    assert ask(client, kb, question="how to deploy the service")["route"] == "procedural"


def test_clear_route_skips_llm_classifier(client, kb, llm, monkeypatch):
    import app.services.graphrag as graphrag

    async def sure(q_vec):
        return "factual"
    monkeypatch.setattr(graphrag, "_route_by_embedding", sure)
    assert ask(client, kb)["route"] == "factual"
    assert "route" not in llm.kinds()


def test_rerank_skipped_when_few_candidates(client, kb, llm):
    llm.handlers["generate"] = cite_first
    body = ask(client, kb, top_k=8)
    assert "rerank" not in llm.kinds()
    assert "rerank_skipped" in body["timings_ms"]


@pytest.mark.parametrize("candidates,top_n,expected", [
    (8, 8, False), (16, 8, False), (17, 8, True), (3, 1, True), (2, 1, False),
])
def test_rerank_threshold(candidates, top_n, expected):
    from app.services.graphrag import should_rerank
    assert should_rerank(candidates, top_n) is expected


def test_rerank_reorders_context(client, kb, llm, monkeypatch):
    import app.services.graphrag as graphrag
    question = "What did we decide about PostgreSQL and the weekly release?"

    # baseline: fusion (RRF) order, no rerank
    monkeypatch.setattr(graphrag, "RERANK_ENABLED", False)
    baseline = ask(client, kb, question=question, top_k=2)["context_nodes"]
    assert len(baseline) == 2
    rrf_top = baseline[0]["title"]

    # rerank on for any candidate count; the fake reranker demotes the RRF winner
    monkeypatch.setattr(graphrag, "RERANK_ENABLED", True)
    monkeypatch.setattr(graphrag, "RERANK_MIN_RATIO", 0)
    captured = {}

    def rerank(messages):
        lines = [l for l in messages[-1]["content"].splitlines() if re.match(r"^\d+: ", l)]
        captured["lines"] = lines
        return json.dumps([0 if rrf_top in l else 10 for l in lines])

    llm.handlers["rerank"] = rerank
    reranked = ask(client, kb, question=question, top_k=2)["context_nodes"]
    assert "rerank" in llm.kinds() and len(captured["lines"]) >= 2
    assert reranked[0]["title"] != rrf_top


def test_unparseable_rerank_keeps_fusion_order(client, kb, llm, monkeypatch):
    import app.services.graphrag as graphrag
    question = "What did we decide about PostgreSQL and the weekly release?"
    monkeypatch.setattr(graphrag, "RERANK_ENABLED", False)
    baseline = [n["id"] for n in ask(client, kb, question=question, top_k=2)["context_nodes"]]
    monkeypatch.setattr(graphrag, "RERANK_ENABLED", True)
    monkeypatch.setattr(graphrag, "RERANK_MIN_RATIO", 0)
    llm.handlers["rerank"] = lambda m: "I think the first one is best"
    assert [n["id"] for n in ask(client, kb, question=question, top_k=2)["context_nodes"]] == baseline


def test_rerank_can_be_disabled(client, kb, llm, monkeypatch):
    import app.services.graphrag as graphrag
    monkeypatch.setattr(graphrag, "RERANK_ENABLED", False)
    llm.handlers["rerank"] = lambda m: pytest.fail("rerank should not be called")
    ask(client, kb, top_k=1)
    assert "rerank" not in llm.kinds()


def test_history_is_passed_to_the_llm(client, kb, llm):
    llm.handlers["generate"] = cite_first
    history = [{"role": "user", "content": "We talked about databases earlier."},
               {"role": "assistant", "content": "Yes, PostgreSQL came up."}]
    ask(client, kb, history=history)
    messages = llm.last("generate")
    assert messages[1:3] == history
    assert messages[-1]["content"].startswith("Context:")


def test_empty_knowledge_base_short_circuits(client, make_user, llm):
    body = ask(client, make_user())
    assert body["retrieval_mode"] == "none"
    assert "No relevant knowledge" in body["answer"]
    assert "generate" not in llm.kinds()


def test_rejected_items_are_not_used(client, kb, llm):
    items = client.get("/knowledge", headers=kb).json()["knowledge_items"]
    pg = [i for i in items if "PostgreSQL" in i["title"]]
    for item in pg:
        client.patch(f"/knowledge/review/{item['id']}", headers=kb, json={"status": "rejected"})
    llm.handlers["generate"] = cite_first
    body = ask(client, kb)
    assert not ({i["id"] for i in pg} & {n["id"] for n in body["context_nodes"]})


def test_query_is_logged(client, kb, llm, db_query):
    llm.handlers["generate"] = cite_first
    question = "Log me: what did we decide about PostgreSQL?"
    body = ask(client, kb, question=question)
    [log] = db_query("SELECT FROM QueryLog WHERE question = :q", q=question)
    assert log["retrieval_mode"] == body["retrieval_mode"]
    assert log["context_node_ids"] == [n["id"] for n in body["context_nodes"]]
    assert log["llm_provider"] == "fake:llm"
    assert log["citations"] == body["citations"]
