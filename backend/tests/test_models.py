"""The three local LLMs: login gate, pre-login download, per-user choice, management rules."""
from __future__ import annotations

import json
import uuid

import pytest

from tests.helpers import ingest_text

CATALOG = ["llama3.1:8b", "mistral:7b", "gpt-oss:20b"]


def register(client):
    username, password = f"user_{uuid.uuid4().hex[:10]}", "secret123"
    assert client.post("/auth/register", json={"username": username, "password": password}).status_code == 201
    return username, password


def login(client, username, password):
    return client.post("/auth/token", data={"username": username, "password": password})


def headers_for(client, username, password):
    r = login(client, username, password)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def sse_events(response):
    return [json.loads(line[len("data: "):]) for line in response.text.splitlines() if line.startswith("data: ")]


# ── catalog (public) ─────────────────────────────────────────────────────────

def test_catalog_is_public_and_lists_exactly_three(client, ollama):
    body = client.get("/models/catalog").json()
    assert [m["id"] for m in body["models"]] == CATALOG
    assert all(m["provider"] == "ollama" for m in body["models"])
    assert body["ollamaReachable"] and body["anyInstalled"]
    assert {m["id"]: m["installed"] for m in body["models"]} == {
        "llama3.1:8b": True, "mistral:7b": False, "gpt-oss:20b": False}
    assert body["recommended"] in CATALOG and body["ramGb"] > 0


def test_catalog_ignores_other_ollama_models(client, ollama):
    ollama.installed = {"llama3.1:8b", "qwen2.5:14b", "phi3:latest"}
    body = client.get("/models/catalog").json()
    assert [m["id"] for m in body["models"]] == CATALOG


def test_catalog_reports_ollama_down(client, ollama):
    ollama.reachable = False
    body = client.get("/models/catalog").json()
    assert body["ollamaReachable"] is False and body["anyInstalled"] is False


# ── login gate ───────────────────────────────────────────────────────────────

def test_login_requires_an_installed_model(client, ollama):
    username, password = register(client)
    ollama.installed = set()
    r = login(client, username, password)
    assert r.status_code == 412 and "Download" in r.json()["detail"]
    ollama.installed = {"mistral:7b"}
    assert login(client, username, password).status_code == 200


def test_other_ollama_models_do_not_satisfy_the_gate(client, ollama):
    username, password = register(client)
    ollama.installed = {"qwen2.5:14b"}
    assert login(client, username, password).status_code == 412


def test_login_reports_ollama_down(client, ollama):
    username, password = register(client)
    ollama.reachable = False
    r = login(client, username, password)
    assert r.status_code == 503 and "Ollama" in r.json()["detail"]


def test_wrong_password_still_says_invalid_credentials(client, ollama):
    username, _ = register(client)
    ollama.installed = set()
    assert login(client, username, "wrong-password").status_code == 401


def test_login_assigns_an_installed_model(client, ollama):
    username, password = register(client)
    ollama.installed = {"gpt-oss:20b"}
    h = headers_for(client, username, password)
    status = client.get("/models/status", headers=h).json()["llm"]
    assert status["model"] == "gpt-oss:20b" and status["installed"]


# ── pre-login download ───────────────────────────────────────────────────────

def test_bootstrap_install_downloads_first_model_without_login(client, ollama):
    ollama.installed = set()
    r = client.post("/models/bootstrap-install", json={"model_id": "mistral:7b"})
    assert r.status_code == 200
    events = sse_events(r)
    assert events[-1] == {"status": "done"}
    assert any(e.get("completed") == 50 for e in events)
    assert ollama.pulled == ["mistral:7b"] and "mistral:7b" in ollama.installed
    assert client.get("/models/catalog").json()["anyInstalled"]


def test_bootstrap_install_is_refused_once_a_model_exists(client, ollama):
    r = client.post("/models/bootstrap-install", json={"model_id": "mistral:7b"})
    assert r.status_code == 409 and ollama.pulled == []


def test_bootstrap_install_only_accepts_catalog_models(client, ollama):
    ollama.installed = set()
    r = client.post("/models/bootstrap-install", json={"model_id": "qwen2.5:14b"})
    assert r.status_code == 400 and ollama.pulled == []


def test_failed_download_reports_an_error(client, ollama):
    ollama.installed = set()
    ollama.fail_pull = True
    events = sse_events(client.post("/models/bootstrap-install", json={"model_id": "mistral:7b"}))
    assert "error" in events[-1] and not ollama.installed


# ── per-user choice ──────────────────────────────────────────────────────────

def test_switching_model_is_used_for_llm_calls_and_persists(client, ollama, llm):
    ollama.installed = {"llama3.1:8b", "mistral:7b"}
    username, password = register(client)
    h = headers_for(client, username, password)
    r = client.post("/models/set-default", headers=h, json={"model_id": "mistral:7b"})
    assert r.status_code == 200 and r.json()["active"] == "mistral:7b"

    llm.reset()
    client.post("/knowledge/graphrag/query", headers=h, json={"question": "anything?"})
    ingest_text(client, h)
    assert llm.models and set(llm.models) == {"mistral:7b"}

    # a new session (fresh login) keeps the choice
    h2 = headers_for(client, username, password)
    assert client.get("/models/status", headers=h2).json()["llm"]["model"] == "mistral:7b"


def test_each_user_gets_their_own_model(client, ollama, llm):
    ollama.installed = {"llama3.1:8b", "mistral:7b"}
    alice, bob = register(client), register(client)
    ha, hb = headers_for(client, *alice), headers_for(client, *bob)
    client.post("/models/set-default", headers=ha, json={"model_id": "mistral:7b"})
    client.post("/models/set-default", headers=hb, json={"model_id": "llama3.1:8b"})

    llm.reset()
    client.post("/knowledge/graphrag/query", headers=ha, json={"question": "a?"})
    alice_models = set(llm.models)
    llm.reset()
    client.post("/knowledge/graphrag/query", headers=hb, json={"question": "b?"})
    assert alice_models == {"mistral:7b"} and set(llm.models) == {"llama3.1:8b"}


def test_cannot_switch_to_a_model_that_is_not_downloaded(client, ollama, make_user):
    h = make_user()
    r = client.post("/models/set-default", headers=h, json={"model_id": "gpt-oss:20b"})
    assert r.status_code == 400


def test_login_moves_user_off_a_model_removed_outside_the_app(client, ollama):
    ollama.installed = {"llama3.1:8b", "mistral:7b"}
    username, password = register(client)
    h = headers_for(client, username, password)
    client.post("/models/set-default", headers=h, json={"model_id": "mistral:7b"})
    ollama.installed = {"llama3.1:8b"}          # e.g. `ollama rm mistral:7b` in a terminal
    h2 = headers_for(client, username, password)
    assert client.get("/models/status", headers=h2).json()["llm"]["model"] == "llama3.1:8b"


# ── management rules ─────────────────────────────────────────────────────────

def test_model_list_has_exactly_three_with_active_flag(client, ollama, make_user):
    ollama.installed = {"llama3.1:8b", "qwen2.5:14b"}
    models = client.get("/models/local", headers=make_user()).json()
    assert [m["id"] for m in models] == CATALOG
    assert [m["id"] for m in models if m["active"]] == ["llama3.1:8b"]


@pytest.mark.parametrize("endpoint", ["/models/install", "/models/remove", "/models/set-default"])
def test_only_catalog_models_can_be_managed(client, ollama, make_user, endpoint):
    r = client.post(endpoint, headers=make_user(role="admin"), json={"model_id": "qwen2.5:14b"})
    assert r.status_code == 400 and ollama.pulled == [] and ollama.deleted == []


@pytest.mark.parametrize("endpoint", ["/models/install", "/models/remove"])
def test_members_cannot_install_or_remove_shared_models(client, ollama, make_user, endpoint):
    ollama.installed = {"llama3.1:8b", "mistral:7b"}
    r = client.post(endpoint, headers=make_user(), json={"model_id": "mistral:7b"})
    assert r.status_code == 403 and ollama.pulled == [] and ollama.deleted == []


def test_members_can_still_choose_their_own_model(client, ollama, make_user):
    ollama.installed = {"llama3.1:8b", "mistral:7b"}
    r = client.post("/models/set-default", headers=make_user(), json={"model_id": "mistral:7b"})
    assert r.status_code == 200


def test_signed_in_install(client, ollama, make_user):
    events = sse_events(client.post("/models/install", headers=make_user(role="admin"), json={"model_id": "gpt-oss:20b"}))
    assert events[-1] == {"status": "done"} and "gpt-oss:20b" in ollama.installed


def test_cannot_remove_the_only_installed_model(client, ollama, make_user):
    r = client.post("/models/remove", headers=make_user(role="admin"), json={"model_id": "llama3.1:8b"})
    assert r.status_code == 409 and ollama.deleted == []


def test_removing_active_model_switches_everyone_using_it(client, ollama, make_user):
    ollama.installed = {"llama3.1:8b", "mistral:7b"}
    ha = make_user(role="admin")
    hb = headers_for(client, *register(client))
    client.post("/models/set-default", headers=ha, json={"model_id": "mistral:7b"})
    client.post("/models/set-default", headers=hb, json={"model_id": "mistral:7b"})

    r = client.post("/models/remove", headers=ha, json={"model_id": "mistral:7b"})
    assert r.status_code == 200 and r.json()["active"] == "llama3.1:8b"
    assert ollama.deleted == ["mistral:7b"]
    # bob shared the machine's Ollama, so he was moved too
    assert client.get("/models/status", headers=hb).json()["llm"]["model"] == "llama3.1:8b"


def test_management_reports_ollama_down(client, ollama, make_user):
    h = make_user(role="admin")
    ollama.reachable = False
    assert client.post("/models/install", headers=h, json={"model_id": "mistral:7b"}).status_code == 503
    assert client.get("/models/status", headers=h).json()["llm"]["ollamaReachable"] is False
