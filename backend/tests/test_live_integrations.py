"""
Live integration tests: the real GitHub, Jira, Linear and Ollama, end to end
through the API (connector saved with encrypted credentials -> background
sync -> stored artifacts). Nothing here is mocked.

Each service runs only when its variables are set, so these never fail for
lack of credentials, and they never run by accident against a service you
didn't name:

  GitHub   KH_LIVE_GITHUB_REPO_URL=https://github.com/owner/name   (public repos need no token)
           KH_LIVE_GITHUB_TOKEN             (optional; needed for private repos)
  Jira     KH_LIVE_JIRA_URL, KH_LIVE_JIRA_TOKEN, KH_LIVE_JIRA_JQL
           KH_LIVE_JIRA_EMAIL               (Cloud; leave unset for Server / Data Center PATs)
  Linear   KH_LIVE_LINEAR_API_URL, KH_LIVE_LINEAR_API_KEY, KH_LIVE_LINEAR_TEAM (optional)
  Ollama   KH_LIVE_OLLAMA=1                 (uses an installed catalog model; can take minutes on CPU)

Run only these:  pytest -m live
Use read-only credentials scoped to test data: syncs only read from the
services, but the tokens are real.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import httpx
import pytest

from tests import conftest

pytestmark = pytest.mark.live


def _env(*names: str) -> Dict[str, str]:
    values = {n: os.getenv(n, "") for n in names}
    missing = [n for n, v in values.items() if not v]
    if missing:
        pytest.skip(f"live test: set {', '.join(missing)} to run it")
    return values


def _create(client, headers, kind: str, config: Dict[str, Any]) -> Dict[str, Any]:
    r = client.post("/connectors", headers=headers, json={"kind": kind, "name": f"live {kind}", "config": config})
    assert r.status_code == 201, r.text
    return r.json()


def _sync(client, headers, connector_id: str) -> Dict[str, Any]:
    assert client.post(f"/connectors/{connector_id}/sync", headers=headers).status_code == 202
    return next(c for c in client.get("/connectors", headers=headers).json() if c["id"] == connector_id)


def _artifacts(client, headers, source_type: str):
    return [a for a in client.get("/knowledge", headers=headers).json()["artifacts"] if a["source_type"] == source_type]


# ── GitHub ──────────────────────────────────────────────────────────────────

def test_live_github_merged_pull_requests(client, alice, db_query):
    env = _env("KH_LIVE_GITHUB_REPO_URL")
    config = {"repo_url": env["KH_LIVE_GITHUB_REPO_URL"], "max_items": 2}
    if os.getenv("KH_LIVE_GITHUB_TOKEN"):
        config["token"] = os.environ["KH_LIVE_GITHUB_TOKEN"]
    conn = _create(client, alice, "github", config)
    if "token" in config:
        stored = db_query("SELECT config FROM Connector WHERE id = :id", id=conn["id"])[0]["config"]
        assert stored["token"].startswith("enc:v1:") and config["token"] not in str(stored)

    status = _sync(client, alice, conn["id"])
    assert status["last_status"] == "ok", status
    result = status["last_result"]
    assert result["fetched"] >= 1, "the repository has no merged pull requests to test with"
    assert result["created"] == result["fetched"]

    prs = _artifacts(client, alice, "github_pr")
    link = env["KH_LIVE_GITHUB_REPO_URL"].lower().removesuffix(".git").rstrip("/")
    for pr in prs:
        assert pr["source"].lower().startswith(f"{link}/pull/")
        assert pr["title"].startswith("PR #") and pr["content"].startswith("# ")
        assert pr["created_at"][:4].isdigit()
    # a second sync against the live API changes nothing
    again = _sync(client, alice, conn["id"])["last_result"]
    assert again["unchanged"] == again["fetched"] and again["created"] == 0


def test_live_github_rejects_a_bad_token(client, alice):
    env = _env("KH_LIVE_GITHUB_REPO_URL")
    conn = _create(client, alice, "github", {"repo_url": env["KH_LIVE_GITHUB_REPO_URL"],
                                             "token": "ghp_" + "0" * 36, "max_items": 1})
    status = _sync(client, alice, conn["id"])
    assert status["last_status"] == "error" and "refused the credentials (401)" in status["last_error"]


# ── Jira ────────────────────────────────────────────────────────────────────

def test_live_jira_issues(client, alice):
    env = _env("KH_LIVE_JIRA_URL", "KH_LIVE_JIRA_TOKEN", "KH_LIVE_JIRA_JQL")
    config = {"base_url": env["KH_LIVE_JIRA_URL"], "api_token": env["KH_LIVE_JIRA_TOKEN"],
              "jql": env["KH_LIVE_JIRA_JQL"], "max_items": 3}
    if os.getenv("KH_LIVE_JIRA_EMAIL"):
        config["email"] = os.environ["KH_LIVE_JIRA_EMAIL"]
    conn = _create(client, alice, "jira", config)
    status = _sync(client, alice, conn["id"])
    assert status["last_status"] == "ok", status
    assert status["last_result"]["fetched"] >= 1, "the JQL matched no issues"
    base = env["KH_LIVE_JIRA_URL"].rstrip("/")
    for issue in _artifacts(client, alice, "jira"):
        assert issue["source"].startswith(f"{base}/browse/")
        assert issue["content"].startswith("# ")


# ── Linear ──────────────────────────────────────────────────────────────────

def test_live_linear_issues(client, alice):
    env = _env("KH_LIVE_LINEAR_API_URL", "KH_LIVE_LINEAR_API_KEY")
    config = {"api_url": env["KH_LIVE_LINEAR_API_URL"], "api_key": env["KH_LIVE_LINEAR_API_KEY"],
              "days": 90, "max_items": 3}
    if os.getenv("KH_LIVE_LINEAR_TEAM"):
        config["team_key"] = os.environ["KH_LIVE_LINEAR_TEAM"]
    conn = _create(client, alice, "linear", config)
    status = _sync(client, alice, conn["id"])
    assert status["last_status"] == "ok", status
    assert status["last_result"]["fetched"] >= 1, "no issues updated in the last 90 days"
    for issue in _artifacts(client, alice, "linear"):
        assert issue["source"].startswith("https://linear.app/")


# ── Ollama (real LLM extraction) ────────────────────────────────────────────

def _installed_catalog_model() -> str:
    from app.services import llm_catalog
    from app.services.providers import OLLAMA_BASE
    try:
        tags = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5).json().get("models", [])
    except httpx.HTTPError as exc:
        pytest.skip(f"live test: Ollama not reachable at {OLLAMA_BASE}: {exc}")
    installed = [m["name"] for m in tags if m.get("name") in llm_catalog.CATALOG_IDS]
    if not installed:
        pytest.skip("live test: none of the supported models is installed in Ollama")
    return installed[0]


def test_live_ollama_extracts_decisions_from_a_transcript(client, make_user, ollama, monkeypatch):
    _env("KH_LIVE_OLLAMA")
    from app.services import providers
    model = _installed_catalog_model()
    ollama.installed = {model}                                  # login assigns the real installed model
    monkeypatch.setattr(providers, "_resolve_llm", conftest.REAL_RESOLVE_LLM)
    headers = make_user()

    r = client.post("/knowledge/artifacts/transcript", headers=headers, json={
        "title": "Architecture sync", "source_type": "transcript", "content": (
            "Ana: We decided to move the billing database from MySQL to PostgreSQL because we need JSON columns.\n"
            "Ben: Agreed. I'll write the migration plan by Friday.\n"
            "Ana: One risk is downtime during the cut-over; we should rehearse it on staging first.\n")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body.get("llm_error"), body.get("llm_error")
    types = {i["type"] for i in body["items"]}
    assert "decision" in types, body["items"]
    stored = client.get("/knowledge", headers=headers).json()["knowledge_items"]
    assert any(i["extraction_engine"] == "local_llm" for i in stored)
