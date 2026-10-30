"""Full-text search details that the broader knowledge tests don't pin down."""
from __future__ import annotations

from app.repositories.search import lucene_query
from tests.helpers import ingest_text


def test_each_word_is_the_whole_term_or_a_prefix():
    assert lucene_query("Billing DB") == "+(billing billing*) +(db db*)"
    assert lucene_query("billing", require_all=False) == "(billing billing*)"
    assert lucene_query("a+b:(c)") == "+(a a*) +(b b*) +(c c*)"          # no Lucene operators get through


def test_a_word_shared_by_several_items_finds_them_all(client, make_user):
    """Regression: a bare prefix query (billing*) found 2 of 3 items that contain the exact word."""
    headers = make_user()
    for title, content in (("2023", "We decided to adopt MySQL for billing."),
                           ("2025", "We decided to move billing to PostgreSQL."),
                           ("2026", "We decided to keep billing invoices.")):
        ingest_text(client, headers, title=title, content=content)
    for q in ("billing", "bill", "BILLING"):
        hits = client.get("/knowledge/search", headers=headers, params={"q": q}).json()["knowledge_items"]
        assert len(hits) == 3, q
