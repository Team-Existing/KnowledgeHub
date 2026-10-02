"""Connectors: parsers, config handling, and syncing folders, git ADRs, GitHub, Jira and Linear."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest

from app.services.connectors.base import ConfigField, ConnectorError, validate_config
from app.services.connectors.git_adr import parse_adr
from app.services.connectors.parsers import adf_to_text, captions_to_transcript


# ── parsers and config (no database) ────────────────────────────────────────

VTT = """WEBVTT

NOTE recorded by Zoom
this block is ignored

1
00:00:01.000 --> 00:00:04.000
<v Ana Lopez>We decided to ship the beta on Friday.</v>

2
00:00:04.500 --> 00:00:06.000
<v Ana Lopez>Pending the load test.</v>

3
00:00:06.500 --> 00:00:09.000
<v Ben>Risk: the load test might slip.</v>
"""

SRT = """1
00:00:01,000 --> 00:00:03,000
Ana: Action item, Ben updates the runbook.

2
00:00:03,500 --> 00:00:05,000
Ben: Will do.
"""


def test_captions_become_speaker_turns():
    assert captions_to_transcript(VTT) == (
        "Ana Lopez: We decided to ship the beta on Friday. Pending the load test.\n"
        "Ben: Risk: the load test might slip."
    )
    assert captions_to_transcript(SRT) == "Ana: Action item, Ben updates the runbook.\nBen: Will do."


def test_adf_to_text():
    adf = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "We agreed to "},
                                          {"type": "text", "text": "drop IE11."}]},
        {"type": "bulletList", "content": [
            {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "one"}]}]},
            {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "two"}]}]},
        ]},
    ]}
    text = adf_to_text(adf)
    assert "We agreed to drop IE11." in text and "- one" in text and "- two" in text
    assert adf_to_text("plain wiki text") == "plain wiki text"


NYGARD_ADR = """# 3. Use PostgreSQL for billing

Date: 2025-03-01

## Status

Accepted

Supersedes [1. Use MySQL for billing](0001-use-mysql-for-billing.md)

## Context

MySQL lacks the JSON features we need.

## Decision

We will use PostgreSQL 16 for the billing store.

## Consequences

Migration takes two sprints.
"""

MADR_ADR = """---
status: superseded by ADR-0004
date: 2024-06-10
deciders: Ana, Ben
---
# Use REST for the public API

## Context and Problem Statement

Partners need a simple API.

## Decision Drivers

* simplicity

## Decision Outcome

Chosen option: REST, because partners know it.
"""


def test_parse_nygard_adr():
    adr = parse_adr("docs/adr/0003-use-postgresql-for-billing.md", NYGARD_ADR)
    assert adr["number"] == 3
    assert adr["title"] == "Use PostgreSQL for billing"
    assert adr["declared_status"] == "active"
    assert adr["date"] == "2025-03-01"
    assert adr["decision"] == "We will use PostgreSQL 16 for the billing store."
    assert "JSON" in adr["context"]
    assert adr["relations"][0] == {"kind": "supersedes", "target": "0001-use-mysql-for-billing.md",
                                   "other_is_newer": False}


def test_parse_madr_adr():
    adr = parse_adr("docs/decisions/0002-use-rest.md", MADR_ADR)
    assert adr["title"] == "Use REST for the public API"
    assert adr["status_word"] == "superseded"
    assert adr["decision"].startswith("Chosen option: REST")        # not "Decision Drivers"
    assert adr["deciders"] == "Ana, Ben"
    assert adr["relations"] == [{"kind": "supersedes", "target": "4", "other_is_newer": True}]
    assert parse_adr("x.md", "no heading here") is None


def test_validate_config():
    fields = [ConfigField("path", "Path", required=True), ConfigField("n", "N", type="number", default=5),
              ConfigField("pats", "Patterns", type="list", default=["*.md"]), ConfigField("x", "X")]
    assert validate_config(fields, {"path": " /a ", "pats": "*.md, *.txt", "junk": 1}) == \
        {"path": "/a", "n": 5, "pats": ["*.md", "*.txt"]}
    with pytest.raises(ConnectorError, match="Path is required"):
        validate_config(fields, {})
    with pytest.raises(ConnectorError, match="whole number"):
        validate_config(fields, {"path": "/a", "n": "many"})


# ── API helpers ─────────────────────────────────────────────────────────────

def create(client, headers, kind: str, config: Dict[str, Any], name: str = "src") -> Dict[str, Any]:
    r = client.post("/connectors", headers=headers, json={"kind": kind, "name": name, "config": config})
    assert r.status_code == 201, r.text
    return r.json()


def sync(client, headers, connector_id: str) -> Dict[str, Any]:
    r = client.post(f"/connectors/{connector_id}/sync", headers=headers)
    assert r.status_code == 202, r.text
    # TestClient runs background tasks before returning, so the sync has finished
    connector = next(c for c in client.get("/connectors", headers=headers).json() if c["id"] == connector_id)
    assert connector["last_status"] in ("ok", "partial"), connector
    return connector


def artifacts(client, headers):
    return client.get("/knowledge", headers=headers).json()


# ── folder ──────────────────────────────────────────────────────────────────

def test_folder_sync_is_idempotent_and_updates_in_place(client, admin, tmp_path: Path):
    (tmp_path / "meetings").mkdir()
    (tmp_path / "meetings" / "standup.vtt").write_text(VTT, encoding="utf-8")
    notes = tmp_path / "notes.md"
    notes.write_text("We decided to freeze deploys on Fridays. Risk: hotfixes get delayed.", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")

    conn = create(client, admin, "folder", {"path": str(tmp_path)})
    first = sync(client, admin, conn["id"])["last_result"]
    assert (first["fetched"], first["created"], first["failed"]) == (2, 2, 0)

    data = artifacts(client, admin)
    by_source = {a["source_type"]: a for a in data["artifacts"]}
    assert set(by_source) == {"transcript", "file"}
    assert "Ana Lopez: We decided to ship the beta on Friday" in by_source["transcript"]["content"]
    assert "meetings" in by_source["transcript"]["tags"] and "folder" in by_source["transcript"]["tags"]
    note_artifact = by_source["file"]
    assert any(i["type"] == "decision" for i in data["knowledge_items"] if i["artifact_id"] == note_artifact["id"])

    second = sync(client, admin, conn["id"])["last_result"]
    assert (second["unchanged"], second["created"], second["updated"]) == (2, 0, 0)

    notes.write_text("We decided to freeze deploys on Fridays and Mondays.", encoding="utf-8")
    third = sync(client, admin, conn["id"])["last_result"]
    assert (third["updated"], third["unchanged"]) == (1, 1)
    after = artifacts(client, admin)
    assert len(after["artifacts"]) == 2                                   # updated, not duplicated
    edited = next(a for a in after["artifacts"] if a["id"] == note_artifact["id"])
    assert "Mondays" in edited["content"]

    docs = client.get(f"/connectors/{conn['id']}/documents", headers=admin).json()
    assert {d["external_id"] for d in docs} == {"meetings/standup.vtt", "notes.md"}


def test_folder_path_must_exist_and_stay_inside_connector_roots(client, admin, tmp_path, monkeypatch):
    # checked when the connector is saved, not only at sync time
    r = client.post("/connectors", headers=admin, json={"kind": "folder", "name": "x",
                                                         "config": {"path": str(tmp_path / "missing")}})
    assert r.status_code == 400 and "not found" in r.json()["detail"]

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("CONNECTOR_ROOTS", str(allowed))
    for path in (str(tmp_path), str(allowed / ".." ), "C:/Windows" if os.name == "nt" else "/etc"):
        r = client.post("/connectors", headers=admin, json={"kind": "folder", "name": "x", "config": {"path": path}})
        assert r.status_code == 400 and "CONNECTOR_ROOTS" in r.json()["detail"], path
    assert client.post("/connectors", headers=admin, json={"kind": "folder", "name": "x",
                                                            "config": {"path": str(allowed)}}).status_code == 201

    # a connector saved before the roots were narrowed can't sync outside them either
    monkeypatch.setenv("CONNECTOR_ROOTS", str(tmp_path))
    conn = create(client, admin, "folder", {"path": str(tmp_path)})
    monkeypatch.setenv("CONNECTOR_ROOTS", str(allowed))
    client.post(f"/connectors/{conn['id']}/sync", headers=admin)
    status = next(c for c in client.get("/connectors", headers=admin).json() if c["id"] == conn["id"])
    assert status["last_status"] == "error" and "CONNECTOR_ROOTS" in status["last_error"]


def test_folder_connectors_are_disabled_without_connector_roots(client, admin, tmp_path, monkeypatch):
    monkeypatch.delenv("CONNECTOR_ROOTS")
    kinds = {k["kind"]: k for k in client.get("/connectors/kinds", headers=admin).json()}
    assert kinds["folder"]["allowed"] is False and "CONNECTOR_ROOTS" in kinds["folder"]["disabled_reason"]
    assert kinds["github"]["allowed"] is True
    r = client.post("/connectors", headers=admin, json={"kind": "folder", "name": "x", "config": {"path": str(tmp_path)}})
    assert r.status_code == 403


def test_symlinks_cannot_escape_the_folder(client, admin, tmp_path):
    inside, outside = tmp_path / "inside", tmp_path / "outside"
    inside.mkdir(); outside.mkdir()
    (outside / "secret.md").write_text("We decided the root password is hunter2.", encoding="utf-8")
    (inside / "ok.md").write_text("We decided to use tabs.", encoding="utf-8")
    try:
        os.symlink(outside / "secret.md", inside / "linked.md")
        os.symlink(outside, inside / "linked_dir", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:   # Windows without symlink privilege
        pytest.skip(f"cannot create symlinks here: {exc}")
    conn = create(client, admin, "folder", {"path": str(inside)})
    sync(client, admin, conn["id"])
    docs = {d["external_id"] for d in client.get(f"/connectors/{conn['id']}/documents", headers=admin).json()}
    assert docs == {"ok.md"}
    assert not any("hunter2" in a["content"] for a in artifacts(client, admin)["artifacts"])


def test_filesystem_connectors_need_admin(client, make_user, tmp_path):
    member = make_user()
    r = client.post("/connectors", headers=member, json={"kind": "folder", "name": "x", "config": {"path": str(tmp_path)}})
    assert r.status_code == 403
    kinds = {k["kind"]: k for k in client.get("/connectors/kinds", headers=member).json()}
    assert kinds["folder"]["allowed"] is False and kinds["git_adr"]["allowed"] is False
    assert kinds["github"]["allowed"] is True


# ── git ADRs → decisions with lineage ───────────────────────────────────────

def test_git_adr_sync_creates_decisions_and_supersede_links(client, admin, tmp_path):
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-use-mysql-for-billing.md").write_text(
        "# 1. Use MySQL for billing\n\nDate: 2023-01-10\n\n## Status\n\nSuperseded by "
        "[3. Use PostgreSQL for billing](0003-use-postgresql-for-billing.md)\n\n## Context\n\nWe need a database.\n\n"
        "## Decision\n\nWe will use MySQL.\n", encoding="utf-8")
    (adr_dir / "0002-use-rest.md").write_text(
        "# 2. Use REST\n\nDate: 2023-05-01\n\n## Status\n\nProposed\n\n## Decision\n\nWe will use REST.\n",
        encoding="utf-8")
    (adr_dir / "0003-use-postgresql-for-billing.md").write_text(NYGARD_ADR, encoding="utf-8")
    (adr_dir / "README.md").write_text("# ADRs\n", encoding="utf-8")

    conn = create(client, admin, "git_adr", {"path": str(tmp_path), "web_url": "https://git.example/acme/blob/main"})
    result = sync(client, admin, conn["id"])["last_result"]
    assert (result["created"], result["links_created"]) == (3, 1)

    reg = client.get("/knowledge/register", headers=admin).json()
    by_title = {i["title"]: i for i in reg["items"]}
    mysql = by_title["ADR-0001: Use MySQL for billing"]
    pg = by_title["ADR-0003: Use PostgreSQL for billing"]
    rest = by_title["ADR-0002: Use REST"]
    assert mysql["status"] == "superseded" and pg["status"] == "active" and rest["status"] == "proposed"
    assert all(i["review_status"] == "accepted" for i in (mysql, pg, rest))
    assert mysql["date"].startswith("2023-01-10") and pg["date"].startswith("2025-03-01")
    assert pg["details"]["why"] == "MySQL lacks the JSON features we need."
    assert any(e["from"] == pg["id"] and e["to"] == mysql["id"] and e["kind"] == "supersedes" and e["origin"] == "git_adr"
               for e in reg["edges"])
    adr_artifact = next(a for a in artifacts(client, admin)["artifacts"] if a["id"] == mysql["artifact_id"])
    assert adr_artifact["source"] == "https://git.example/acme/blob/main/docs/adr/0001-use-mysql-for-billing.md"

    # re-sync: nothing new, link not duplicated
    again = sync(client, admin, conn["id"])["last_result"]
    assert (again["unchanged"], again["links_created"]) == (3, 0)


@pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")
def test_git_adr_dates_and_authors_come_from_real_git_history(client, admin, tmp_path):
    """No mocks: a real repository, real commits, real `git log`."""
    adr_dir = tmp_path / "docs" / "decisions"
    adr_dir.mkdir(parents=True)

    def git(*args, date=None, author="Ana Lopez <ana@example.com>"):
        env = {**os.environ, "GIT_AUTHOR_NAME": author.split(" <")[0], "GIT_AUTHOR_EMAIL": author.split("<")[1][:-1],
               "GIT_COMMITTER_NAME": "ci", "GIT_COMMITTER_EMAIL": "ci@example.com"}
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, env=env)

    git("init", "-q")
    # no "Date:" line, so the date must come from the commit that added the file
    (adr_dir / "0001-use-kafka.md").write_text(
        "# 1. Use Kafka\n\n## Status\n\nAccepted\n\n## Decision\n\nWe will use Kafka for events.\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "ADR 1", date="2022-04-05T10:00:00+00:00")
    (adr_dir / "0002-use-nats.md").write_text(
        "# 2. Use NATS\n\n## Status\n\nAccepted\n\nSupersedes [ADR-0001](0001-use-kafka.md)\n\n"
        "## Decision\n\nWe will move events to NATS.\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "ADR 2", date="2024-09-01T09:30:00+00:00", author="Ben Ode <ben@example.com>")

    conn = create(client, admin, "git_adr", {"path": str(tmp_path)})
    result = sync(client, admin, conn["id"])["last_result"]
    assert (result["created"], result["links_created"]) == (2, 1)

    items = {i["title"]: i for i in client.get("/knowledge/register", headers=admin).json()["items"]}
    kafka, nats = items["ADR-0001: Use Kafka"], items["ADR-0002: Use NATS"]
    assert kafka["date"].startswith("2022-04-05") and nats["date"].startswith("2024-09-01")
    assert kafka["details"]["who"] == "Ana Lopez" and nats["details"]["who"] == "Ben Ode"
    assert kafka["status"] == "superseded" and nats["status"] == "active"


def test_hostile_repo_config_does_not_run_programs(client, admin, tmp_path):
    """A scanned repo's .git/config must not make the server execute anything."""
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    canary = tmp_path / "pwned.txt"
    hook = f'echo pwned > "{canary.as_posix()}"'
    with open(tmp_path / ".git" / "config", "a", encoding="utf-8") as fh:
        fh.write(f"[core]\n\tfsmonitor = {hook}\n\tpager = {hook}\n")
    (tmp_path / "adr").mkdir()
    (tmp_path / "adr" / "0001-x.md").write_text("# 1. X\n\n## Decision\n\nWe will X.\n", encoding="utf-8")
    conn = create(client, admin, "git_adr", {"path": str(tmp_path)})
    sync(client, admin, conn["id"])
    assert not canary.exists()


# ── GitHub / Jira / Linear: offline contract tests (mocked HTTP) ────────────
# These pin down request shapes, pagination and error handling (401s, GraphQL
# errors) that a live service can't be made to produce on demand. The same
# connectors run against the real services in test_live_integrations.py.

def test_github_sync_ingests_merged_prs_with_discussion(client, alice, mock_http):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/api/pulls":
            if request.url.params["page"] != "1":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[
                {"number": 7, "title": "Switch queue to Kafka", "body": "We decided to move to Kafka for replay.",
                 "merged_at": "2025-02-01T10:00:00Z", "html_url": "https://github.com/acme/api/pull/7",
                 "user": {"login": "ana"}, "labels": [{"name": "architecture"}],
                 "comments_url": "https://api.github.com/repos/acme/api/issues/7/comments"},
                {"number": 8, "title": "Closed without merge", "body": "nope", "merged_at": None,
                 "user": {"login": "ben"}},
            ])
        if request.url.path == "/repos/acme/api/issues/7/comments":
            return httpx.Response(200, json=[{"body": "Risk: Kafka ops cost.", "user": {"login": "ben"}}])
        return httpx.Response(404)
    seen = mock_http(handler)

    conn = create(client, alice, "github", {"repo_url": "https://github.com/acme/api", "token": "ghp_secret123"})
    assert conn["config"]["token"] == "********"
    result = sync(client, alice, conn["id"])["last_result"]
    assert (result["fetched"], result["created"]) == (1, 1)
    assert seen[0].headers["Authorization"] == "Bearer ghp_secret123"
    assert {r.url.host for r in seen} == {"api.github.com"}

    art = next(a for a in artifacts(client, alice)["artifacts"] if a["source_type"] == "github_pr")
    assert art["title"] == "PR #7: Switch queue to Kafka"
    assert art["source"] == "https://github.com/acme/api/pull/7"
    assert "ben: Risk: Kafka ops cost." in art["content"]
    assert art["created_at"].startswith("2025-02-01")
    assert {"github", "architecture"} <= set(art["tags"])


def test_jira_cloud_sync_reads_adf_descriptions_and_comments(client, alice, mock_http):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/search/jql"
        assert request.url.params["jql"] == "project = ARCH"
        return httpx.Response(200, json={"isLast": True, "issues": [{"key": "ARCH-12", "fields": {
            "summary": "Pick a cache", "created": "2025-01-05T09:00:00.000+0000",
            "status": {"name": "Done"}, "resolution": {"name": "Fixed"}, "issuetype": {"name": "Task"},
            "reporter": {"displayName": "Ana"}, "labels": ["decision"],
            "description": {"type": "doc", "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "We decided to use Redis."}]}]},
            "comment": {"comments": [{"author": {"displayName": "Ben"}, "created": "2025-01-06T00:00:00",
                                      "body": {"type": "doc", "content": [{"type": "paragraph", "content": [
                                          {"type": "text", "text": "Risk: memory limits."}]}]}}]},
        }}]})
    seen = mock_http(handler)
    conn = create(client, alice, "jira", {"base_url": "https://acme.atlassian.net", "email": "ana@acme.io",
                                         "api_token": "tok", "jql": "project = ARCH"})
    result = sync(client, alice, conn["id"])["last_result"]
    assert result["created"] == 1
    assert seen[0].headers["Authorization"].startswith("Basic ")
    art = next(a for a in artifacts(client, alice)["artifacts"] if a["source_type"] == "jira")
    assert art["source"] == "https://acme.atlassian.net/browse/ARCH-12"
    assert "We decided to use Redis." in art["content"] and "Ben (2025-01-06): Risk: memory limits." in art["content"]


def test_linear_sync_and_auth_errors(client, alice, mock_http):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.headers["Authorization"] != "lin_api_good":
            return httpx.Response(401, json={"errors": [{"message": "auth"}]})
        body = json.loads(request.content)
        assert body["variables"]["filter"]["team"] == {"key": {"eq": "ENG"}}
        return httpx.Response(200, json={"data": {"issues": {
            "nodes": [{"identifier": "ENG-3", "title": "Adopt feature flags", "url": "https://linear.app/x/ENG-3",
                       "description": "We agreed to adopt feature flags for risky releases.",
                       "createdAt": "2025-04-01T00:00:00Z", "state": {"name": "Done"}, "creator": {"name": "Ana"},
                       "labels": {"nodes": []}, "comments": {"nodes": []}}],
            "pageInfo": {"hasNextPage": False, "endCursor": None}}}})
    mock_http(handler)

    bad = create(client, alice, "linear", {"api_url": LINEAR, "api_key": "lin_api_bad", "team_key": "ENG"})
    client.post(f"/connectors/{bad['id']}/sync", headers=alice)
    status = next(c for c in client.get("/connectors", headers=alice).json() if c["id"] == bad["id"])
    assert status["last_status"] == "error" and "refused the credentials" in status["last_error"]

    # fixing the key via PATCH; a masked secret in a later PATCH keeps the stored one
    r = client.patch(f"/connectors/{bad['id']}", headers=alice, json={"config": {"api_url": LINEAR, "api_key": "lin_api_good", "team_key": "ENG"}})
    assert r.status_code == 200 and r.json()["config"]["api_key"] == "********"
    client.patch(f"/connectors/{bad['id']}", headers=alice, json={"config": {"api_url": LINEAR, "api_key": "********", "team_key": "ENG"}})
    result = sync(client, alice, bad["id"])["last_result"]
    assert result["created"] == 1
    art = next(a for a in artifacts(client, alice)["artifacts"] if a["source_type"] == "linear")
    assert art["title"] == "ENG-3: Adopt feature flags"


# ── only the addresses the user provided are ever contacted ──────────────────

LINEAR = "https://api.linear.app/graphql"


@pytest.mark.parametrize("link, repo, api", [
    ("https://github.com/acme/api", "acme/api", "https://api.github.com"),
    ("https://github.com/acme/api.git", "acme/api", "https://api.github.com"),
    ("https://github.com/acme/api/tree/main/docs", "acme/api", "https://api.github.com"),
    ("https://git.acme.internal/platform/billing", "platform/billing", "https://git.acme.internal/api/v3"),
])
def test_github_api_address_comes_from_the_users_link(link, repo, api):
    from app.services.connectors import github
    assert github.resolve({"repo_url": link})[:2] == (repo, api)


def test_connectors_have_no_built_in_endpoints():
    from app.services.connectors import REGISTRY
    for module in REGISTRY.values():
        for field in module.FIELDS:
            assert not (isinstance(field.default, str) and field.default.startswith("http")), \
                f"{module.KIND}.{field.name} defaults to {field.default}"
    for kind in ("github", "jira", "linear"):
        assert any(f.required and "url" in f.name for f in REGISTRY[kind].FIELDS), kind


@pytest.mark.parametrize("kind, config", [
    ("github", {"repo_url": "acme/api"}),                         # not a link
    ("github", {"repo_url": "https://github.com/acme"}),          # not a repository
    ("github", {"repo_url": "ftp://github.com/acme/api"}),
    ("linear", {"api_key": "k"}),                                 # no address at all
    ("jira", {"base_url": "acme.atlassian.net", "api_token": "t"}),
])
def test_addresses_are_required_and_checked_when_saving(client, alice, kind, config):
    r = client.post("/connectors", headers=alice, json={"kind": kind, "name": "x", "config": config})
    assert r.status_code == 400, r.text


def test_github_enterprise_requests_go_only_to_that_host(client, alice, mock_http):
    seen = mock_http(lambda request: httpx.Response(200, json=[]))
    conn = create(client, alice, "github", {"repo_url": "https://git.acme.internal/platform/billing"})
    sync(client, alice, conn["id"])
    assert seen and {(r.url.host, r.url.path) for r in seen} == \
        {("git.acme.internal", "/api/v3/repos/platform/billing/pulls")}


def test_urls_returned_by_a_service_cannot_redirect_the_connector(client, alice, mock_http):
    """A comments_url pointing at another host is refused before any request is sent."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/api/pulls":
            return httpx.Response(200, json=[{
                "number": 1, "title": "t", "body": "b", "merged_at": "2025-01-01T00:00:00Z", "user": {"login": "a"},
                "comments_url": "https://collector.evil.example/steal"}])
        return httpx.Response(404)
    seen = mock_http(handler)
    conn = create(client, alice, "github", {"repo_url": "https://github.com/acme/api", "token": "ghp_secret"})
    client.post(f"/connectors/{conn['id']}/sync", headers=alice)
    status = next(c for c in client.get("/connectors", headers=alice).json() if c["id"] == conn["id"])
    assert status["last_status"] == "error" and "Refused to contact collector.evil.example" in status["last_error"]
    assert {r.url.host for r in seen} == {"api.github.com"}      # the token never went to the other host


def test_redirects_to_other_hosts_are_refused(client, alice, mock_http):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "jira.acme.io":
            return httpx.Response(302, headers={"Location": "https://elsewhere.example/rest/api/2/search"})
        return httpx.Response(200, json={"issues": []})
    seen = mock_http(handler)
    conn = create(client, alice, "jira", {"base_url": "https://jira.acme.io", "api_token": "t", "jql": "x"})
    client.post(f"/connectors/{conn['id']}/sync", headers=alice)
    status = next(c for c in client.get("/connectors", headers=alice).json() if c["id"] == conn["id"])
    assert status["last_status"] == "error" and "elsewhere.example" in status["last_error"]
    assert {r.url.host for r in seen} == {"jira.acme.io"}


def test_old_connectors_are_moved_to_explicit_addresses(client, alice, db_query):
    import uuid
    from app import migrations
    from app.arcadedb import ArcadeSession
    from app.db import get_client
    me = client.get("/auth/me", headers=alice).json()
    ids = {k: f"conn_old_{k}_{uuid.uuid4().hex[:6]}" for k in ("gh", "ghe", "lin")}
    for key, kind, config in [("gh", "github", {"repo": "acme/api", "api_url": "https://api.github.com"}),
                              ("ghe", "github", {"repo": "plat/billing", "api_url": "https://git.acme.internal/api/v3"}),
                              ("lin", "linear", {"team_key": "ENG"})]:
        db_query("INSERT INTO Connector CONTENT :d", d={"id": ids[key], "user_id": me["id"], "kind": kind,
                                                       "name": key, "config": config})

    async def go():
        session = ArcadeSession(get_client())
        try:
            return await migrations.migrate_connector_addresses(session)
        finally:
            await session.close()
    assert client.portal.call(go) >= 3
    cfg = {k: db_query("SELECT config FROM Connector WHERE id = :id", id=i)[0]["config"] for k, i in ids.items()}
    assert cfg["gh"]["repo_url"] == "https://github.com/acme/api" and "repo" not in cfg["gh"]
    assert cfg["ghe"]["repo_url"] == "https://git.acme.internal/plat/billing"
    assert cfg["lin"]["api_url"] == LINEAR


# ── management and isolation ────────────────────────────────────────────────

def test_connector_validation_and_isolation(client, admin, bob, tmp_path):
    assert client.post("/connectors", headers=admin, json={"kind": "dropbox", "name": "x"}).status_code == 400
    assert client.post("/connectors", headers=admin, json={"kind": "jira", "name": "x",
                                                            "config": {"base_url": "https://j"}}).status_code == 400
    (tmp_path / "a.md").write_text("We decided to use tabs.", encoding="utf-8")
    conn = create(client, admin, "folder", {"path": str(tmp_path)})
    assert conn["id"] not in {c["id"] for c in client.get("/connectors", headers=bob).json()}
    for method, path in [("POST", f"/connectors/{conn['id']}/sync"), ("PATCH", f"/connectors/{conn['id']}"),
                         ("DELETE", f"/connectors/{conn['id']}"), ("GET", f"/connectors/{conn['id']}/documents")]:
        assert client.request(method, path, headers=bob, json={}).status_code == 404, path


def test_delete_connector_optionally_removes_its_artifacts(client, admin, tmp_path):
    (tmp_path / "a.md").write_text("We decided to use tabs.", encoding="utf-8")
    keep = create(client, admin, "folder", {"path": str(tmp_path)}, name="keep")
    sync(client, admin, keep["id"])
    assert client.delete(f"/connectors/{keep['id']}", headers=admin).status_code == 204
    assert len(artifacts(client, admin)["artifacts"]) == 1

    drop = create(client, admin, "folder", {"path": str(tmp_path)}, name="drop")
    sync(client, admin, drop["id"])
    assert len(artifacts(client, admin)["artifacts"]) == 2      # separate connector, separate artifact
    assert client.delete(f"/connectors/{drop['id']}?delete_artifacts=true", headers=admin).status_code == 204
    assert len(artifacts(client, admin)["artifacts"]) == 1
