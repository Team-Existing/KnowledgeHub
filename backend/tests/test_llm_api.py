"""Models behind an OpenAI-compatible API (LLM_API_*): the provider, and choosing them alongside local models."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

import httpx
import pytest

from app.services import providers
from tests.helpers import ingest_text


@pytest.fixture
def api(monkeypatch):
    """An API with two models, named "Test Cloud"."""
    monkeypatch.setattr(providers, "LLM_API_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setattr(providers, "LLM_API_KEY", "sk-test-key")
    monkeypatch.setattr(providers, "LLM_API_MODELS", ["small-model", "big-model"])
    monkeypatch.setattr(providers, "LLM_API_NAME", "Test Cloud")
    monkeypatch.delenv("LLM_DEFAULT_MODEL", raising=False)
    return providers


@pytest.fixture
def no_ollama(monkeypatch):
    monkeypatch.setattr(providers, "OLLAMA_ENABLED", False)


@pytest.fixture
def server(monkeypatch, api):
    """The API server: answers with handler(request body), recording each request."""
    requests = []
    state = {"handler": lambda body: completion("ok")}

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append((request, body))
        reply = state["handler"](body)
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

    transport = httpx.MockTransport(respond)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(providers.OpenAICompatibleProvider, "_newer_params", set())
    monkeypatch.setattr(providers.OpenAICompatibleProvider, "_no_schema", set())

    async def no_wait(_seconds):
        return None
    monkeypatch.setattr(providers.asyncio, "sleep", no_wait)

    def set_handler(handler):
        state["handler"] = handler
    return requests, set_handler


def completion(content, finish="stop", **usage):
    return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": usage or {"prompt_tokens": 12, "completion_tokens": 3}}


def chat(model="small-model", **kwargs):
    provider = providers.provider_for(f"api/{model}")
    return asyncio.run(provider.chat([{"role": "user", "content": "hi"}], **kwargs))


# ── the provider ─────────────────────────────────────────────────────────────

def test_api_model_ids_route_to_the_api_and_tags_to_ollama(api):
    assert isinstance(providers.provider_for("api/small-model"), providers.OpenAICompatibleProvider)
    assert providers.provider_for("api/small-model").is_local is False
    assert isinstance(providers.provider_for("llama3.1:8b"), providers.OllamaProvider)
    assert providers.api_model_ids() == ["api/small-model", "api/big-model"]


def test_chat_sends_an_openai_request_with_the_key(server, caplog):
    requests, _ = server
    with caplog.at_level(logging.INFO, logger="app.services.providers"):
        assert chat(max_tokens=50, temperature=0.2) == "ok"
    request, body = requests[0]
    assert str(request.url) == "https://llm.example.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test-key"
    assert body == {"model": "small-model", "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 50, "temperature": 0.2}
    assert any("small-model (Test Cloud)" in r.getMessage() and "12 prompt + 3 reply tokens" in r.getMessage()
               for r in caplog.records)
    assert "sk-test-key" not in caplog.text


def test_json_modes(server):
    requests, _ = server
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    chat(json_mode=True)
    chat(json_mode=schema)
    assert requests[0][1]["response_format"] == {"type": "json_object"}
    assert requests[1][1]["response_format"] == {"type": "json_schema",
                                                 "json_schema": {"name": "reply", "schema": schema}}


def test_a_model_that_wants_newer_parameters_gets_them_from_then_on(server):
    requests, set_handler = server
    set_handler(lambda body: httpx.Response(400, json={"error": {
        "message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}})
        if "max_tokens" in body else completion("ok"))
    assert chat(max_tokens=40) == "ok"
    assert chat(max_tokens=40) == "ok"
    sent = [body for _, body in requests]
    assert len(sent) == 3                    # one refusal, then the newer form straight away
    assert sent[1] == sent[2] == {"model": "small-model", "messages": [{"role": "user", "content": "hi"}],
                                  "max_completion_tokens": 40}


def test_a_server_without_json_schema_falls_back_to_json_object(server):
    requests, set_handler = server
    set_handler(lambda body: httpx.Response(400, text="response_format json_schema is not supported")
                if body.get("response_format", {}).get("type") == "json_schema" else completion("{}"))
    assert chat(json_mode={"type": "object"}) == "{}"
    assert requests[-1][1]["response_format"] == {"type": "json_object"}


def test_rate_limits_and_outages_are_retried_briefly(server):
    requests, set_handler = server
    replies = iter([httpx.Response(429, headers={"retry-after": "1"}), httpx.Response(503), completion("ok")])
    set_handler(lambda body: next(replies))
    assert chat() == "ok" and len(requests) == 3

    requests.clear()
    set_handler(lambda body: httpx.Response(503))
    assert chat() is None and len(requests) == 3       # gives up instead of retrying forever


def test_errors_and_cut_off_replies(server, caplog):
    requests, set_handler = server
    set_handler(lambda body: httpx.Response(401, json={"error": {"message": "invalid api key"}}))
    with caplog.at_level(logging.INFO, logger="app.services.providers"):
        assert chat() is None
        set_handler(lambda body: completion("Cut sho", finish="length"))
        assert chat(max_tokens=10) == "Cut sho"
        set_handler(lambda body: completion(None))
        assert chat() is None
    assert len(requests) == 3                             # a 401 is not retried
    messages = [r.getMessage() for r in caplog.records]
    assert any("HTTP 401" in m for m in messages)
    assert "LLM small-model stopped at the 10-token reply limit; the reply is cut short" in messages


def test_unreachable_api_returns_none(monkeypatch, api):
    def refuse(request):
        raise httpx.ConnectError("connection refused")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(refuse), **kw))
    assert chat() is None


def test_nothing_is_offered_without_a_model_list(monkeypatch):
    monkeypatch.setattr(providers, "LLM_API_MODELS", [])
    assert providers.api_model_ids() == [] and not providers.is_api_model("api/gpt-4.1")


# ── choosing API models ──────────────────────────────────────────────────────

def register_and_login(client):
    username = f"user_{uuid.uuid4().hex[:10]}"
    assert client.post("/auth/register", json={"username": username, "password": "secret123"}).status_code == 201
    return client.post("/auth/token", data={"username": username, "password": "secret123"})


def headers(r):
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_catalog_lists_api_models_after_the_local_ones(client, api):
    body = client.get("/models/catalog").json()
    models = body["models"]
    assert [m["id"] for m in models] == ["llama3.1:8b", "mistral:7b", "gpt-oss:20b", "api/small-model", "api/big-model"]
    cloud = models[3]
    assert cloud["provider"] == "api" and cloud["local"] is False and cloud["installed"] is True
    assert cloud["providerName"] == "Test Cloud" and "sent there" in cloud["description"]
    assert models[0]["local"] is True and body["ollamaEnabled"] is True


def test_api_models_let_users_sign_in_while_ollama_is_down(client, api, ollama):
    ollama.reachable = False
    assert client.get("/models/catalog").json()["anyInstalled"] is True
    h = headers(register_and_login(client))
    status = client.get("/models/status", headers=h).json()["llm"]
    assert status == {"provider": "api", "local": False, "providerName": "Test Cloud",
                      "model": "api/small-model", "installed": True, "ollamaReachable": False}


def test_choosing_an_api_model_routes_the_users_llm_calls(client, api, ollama, llm):
    h = headers(register_and_login(client))
    r = client.post("/models/set-default", headers=h, json={"model_id": "api/big-model"})
    assert r.status_code == 200 and r.json()["active"] == "api/big-model"
    llm.reset()
    ingest_text(client, h)
    client.post("/knowledge/graphrag/query", headers=h, json={"question": "anything?"})
    assert llm.models and set(llm.models) == {"api/big-model"}
    assert [m["id"] for m in client.get("/models/local", headers=h).json() if m["active"]] == ["api/big-model"]


def test_unknown_api_models_cannot_be_chosen(client, api, make_user):
    r = client.post("/models/set-default", headers=make_user(), json={"model_id": "api/other-model"})
    assert r.status_code == 400 and "api/small-model" in r.json()["detail"]


def test_the_last_local_model_can_be_removed_when_api_models_remain(client, api, ollama, make_user):
    r = client.post("/models/remove", headers=make_user(role="admin"), json={"model_id": "llama3.1:8b"})
    assert r.status_code == 200 and r.json()["active"] == "api/small-model"


def test_without_ollama_only_api_models_exist(client, api, no_ollama, ollama, make_user):
    ollama.reachable = False          # never asked: availability() must not call it
    body = client.get("/models/catalog").json()
    assert body["ollamaEnabled"] is False and body["anyInstalled"] is True
    assert [m["id"] for m in body["models"]] == ["api/small-model", "api/big-model"]
    admin = make_user(role="admin")
    for endpoint in ("/models/install", "/models/remove", "/models/set-default"):
        r = client.post(endpoint, headers=admin, json={"model_id": "llama3.1:8b"})
        assert r.status_code == 400, endpoint
    assert ollama.pulled == [] and ollama.deleted == []
    assert providers.default_model() == "api/small-model"


def test_without_any_model_sign_in_explains_who_can_fix_it(client, no_ollama, monkeypatch):
    monkeypatch.setattr(providers, "LLM_API_MODELS", [])
    r = register_and_login(client)
    assert r.status_code == 412 and "administrator" in r.json()["detail"]
