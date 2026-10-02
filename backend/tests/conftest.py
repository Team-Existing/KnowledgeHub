"""
Test harness: a throwaway ArcadeDB database, and fake LLM / embedding
providers — no Ollama or sentence-transformers needed.

Needs a running ArcadeDB server. Configure it with
  KH_TEST_ARCADEDB_URL       (default http://localhost:2480)
  KH_TEST_ARCADEDB_USER      (default root)
  KH_TEST_ARCADEDB_PASSWORD  (required)
A uniquely named database is created for the run and dropped afterwards. If
the server can't be reached every test is skipped, with the reason shown.

Environment is set before the app is imported. load_dotenv() never
overrides variables that are already set, so a local backend/.env can't
point the tests at your real database, signing key or credentials key.

Roles: the session registers a bootstrap admin first, so every account a
test creates with make_user() is a member unless it asks for role="admin".

Live integrations (real GitHub / Jira / Linear / Ollama) are in
test_live_integrations.py and run only when their KH_LIVE_* variables are set.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import secrets
import tempfile
import uuid
from typing import Any, Callable, Dict, List, Optional

import httpx

ARCADEDB_URL = os.getenv("KH_TEST_ARCADEDB_URL", "http://localhost:2480").rstrip("/")
ARCADEDB_AUTH = (os.getenv("KH_TEST_ARCADEDB_USER", "root"), os.getenv("KH_TEST_ARCADEDB_PASSWORD", ""))
TEST_DATABASE = f"kh_test_{uuid.uuid4().hex[:8]}"
os.environ["ARCADEDB_URL"] = ARCADEDB_URL
os.environ["ARCADEDB_USER"] = ARCADEDB_AUTH[0]
os.environ["ARCADEDB_PASSWORD"] = ARCADEDB_AUTH[1]
os.environ["ARCADEDB_DATABASE"] = TEST_DATABASE
os.environ["MAX_UPLOAD_MB"] = "1"
os.environ["GRAPHRAG_RERANK"] = "on"
os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["ALLOW_REGISTRATION"] = "true"
# the inactivity job would race the tests on the shared database; tests run it explicitly
os.environ["RETENTION_ENABLED"] = "false"
# folder connectors may read the temp dir only (pytest's tmp_path lives under it)
os.environ["CONNECTOR_ROOTS"] = tempfile.gettempdir()

import pytest  # noqa: E402

import app.services.providers as providers  # noqa: E402
from app import crypto  # noqa: E402

os.environ["CREDENTIALS_KEY"] = crypto.generate_key()

Messages = List[Dict[str, str]]
Handler = Callable[[Messages], Optional[str]]


class FakeLLM:
    """
    Records every chat call, classified by which prompt sent it. Each kind
    answers None (= LLM unreachable) unless a test installs a handler.
    """
    is_local = True
    name = "fake:llm"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.calls: List[tuple[str, Messages]] = []
        self.models: List[str] = []          # the active model each call ran under
        self.handlers: Dict[str, Handler] = {}

    @staticmethod
    def kind(messages: Messages) -> str:
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        user = messages[-1]["content"]
        if "document excerpt" in system:
            return "document"
        if "Extract structured knowledge from the text below" in system:
            return "transcript"
        if system.startswith("Summarise the document"):
            return "summary"
        if "hypothetical answer document" in user:
            return "transform"
        if "Classify the question" in user:
            return "route"
        if "Rate each candidate" in user:
            return "rerank"
        if user.startswith("Context:"):
            return "generate"
        return "unknown"

    async def chat(self, messages: Messages, temperature: float = 0,
                   max_tokens: int = 1000, json_mode: bool = False) -> Optional[str]:
        kind = self.kind(messages)
        self.calls.append((kind, messages))
        self.models.append(providers.active_model())
        handler = self.handlers.get(kind)
        return handler(messages) if handler else None

    def kinds(self) -> List[str]:
        return [k for k, _ in self.calls]

    def last(self, kind: str) -> Messages:
        return [m for k, m in self.calls if k == kind][-1]


def bag_of_words_vector(text: str, dims: int = 64) -> List[float]:
    """Deterministic embedding: texts sharing words get high cosine similarity."""
    vec = [0.0] * dims
    for word in re.findall(r"\w+", text.lower()):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % dims] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeEmbeddings:
    is_local = True
    name = "fake:bow"
    dimensions = 64

    async def embed(self, texts: List[str]) -> List[Optional[List[float]]]:
        return [bag_of_words_vector(t) for t in texts]


FAKE_LLM = FakeLLM()
FAKE_EMBEDDINGS = FakeEmbeddings()
# the real resolvers, for live tests that opt back in (see test_live_integrations.py)
REAL_RESOLVE_LLM = providers._resolve_llm
providers._resolve_llm = lambda workspace=None: FAKE_LLM
providers._resolve_embedding = lambda workspace=None: FAKE_EMBEDDINGS

import app.services.llm_catalog as llm_catalog  # noqa: E402


class FakeOllama:
    """Stands in for the local Ollama server: installed models, downloads, deletes."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.installed = {"llama3.1:8b"}
        self.reachable = True
        self.pulled: List[str] = []
        self.deleted: List[str] = []
        self.fail_pull = False

    async def installed_models(self) -> set:
        if not self.reachable:
            raise llm_catalog.OllamaUnavailable("Ollama is not reachable (fake)")
        # like the real one: other models Ollama may have are ignored
        return {m for m in self.installed if m in llm_catalog.CATALOG_IDS}

    async def pull_events(self, model_id: str):
        self.pulled.append(model_id)
        yield {"status": "pulling manifest"}
        yield {"status": "downloading", "total": 100, "completed": 50}
        if self.fail_pull:
            raise RuntimeError("network dropped")
        yield {"status": "success"}
        self.installed.add(model_id)

    async def delete_model(self, model_id: str) -> None:
        self.deleted.append(model_id)
        self.installed.discard(model_id)


FAKE_OLLAMA = FakeOllama()
llm_catalog.installed_models = FAKE_OLLAMA.installed_models
llm_catalog.pull_events = FAKE_OLLAMA.pull_events
llm_catalog.delete_model = FAKE_OLLAMA.delete_model

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _arcadedb_unavailable() -> Optional[str]:
    if not ARCADEDB_AUTH[1]:
        return "set KH_TEST_ARCADEDB_PASSWORD to run the tests against ArcadeDB"
    try:
        # /ready is cheap; /databases also proves the credentials work
        httpx.get(f"{ARCADEDB_URL}/api/v1/ready", timeout=10).raise_for_status()
        r = httpx.get(f"{ARCADEDB_URL}/api/v1/databases", auth=ARCADEDB_AUTH, timeout=30)
    except httpx.HTTPError as exc:
        return f"ArcadeDB not reachable at {ARCADEDB_URL}: {exc}"
    return None if r.status_code == 200 else f"ArcadeDB at {ARCADEDB_URL} answered {r.status_code}"


@pytest.fixture(scope="session")
def client():
    reason = _arcadedb_unavailable()
    if reason:
        pytest.skip(reason)
    try:
        with TestClient(app) as c:     # startup creates TEST_DATABASE and applies the schema
            # claim "first account = admin" now, so test users are members unless they ask otherwise
            r = c.post("/auth/register", json={"username": f"bootstrap_admin_{uuid.uuid4().hex[:6]}",
                                               "password": "secret123"})
            assert r.status_code == 201 and r.json()["role"] == "admin", r.text
            yield c
    finally:
        httpx.post(f"{ARCADEDB_URL}/api/v1/server", auth=ARCADEDB_AUTH,
                   json={"command": f"drop database {TEST_DATABASE}"}, timeout=30)


@pytest.fixture
def db_query(client) -> Callable[..., List[Dict[str, Any]]]:
    """Run a read query directly against the test database (for asserting stored state)."""
    def _query(sql: str, **params: Any) -> List[Dict[str, Any]]:
        r = httpx.post(f"{ARCADEDB_URL}/api/v1/command/{TEST_DATABASE}", auth=ARCADEDB_AUTH, timeout=30,
                       json={"language": "sql", "command": sql, "params": params})
        assert r.status_code == 200, r.text
        return r.json()["result"]
    return _query


@pytest.fixture(autouse=True)
def ollama() -> FakeOllama:
    FAKE_OLLAMA.reset()
    return FAKE_OLLAMA


@pytest.fixture(autouse=True)
def llm() -> FakeLLM:
    FAKE_LLM.reset()
    return FAKE_LLM


def set_role(user_id: str, role: str) -> None:
    r = httpx.post(f"{ARCADEDB_URL}/api/v1/command/{TEST_DATABASE}", auth=ARCADEDB_AUTH, timeout=30,
                   json={"language": "sql", "command": "UPDATE User SET role = :r WHERE id = :id",
                         "params": {"r": role, "id": user_id}})
    assert r.status_code == 200, r.text


@pytest.fixture
def make_user(client) -> Callable[..., Dict[str, str]]:
    """
    Register a fresh user (a member, unless role="admin"); returns auth headers.
    Unique per call so tests share one DB safely.
    """
    def _make(role: str = "member") -> Dict[str, str]:
        username = f"user_{uuid.uuid4().hex[:10]}"
        r = client.post("/auth/register", json={"username": username, "password": "secret123"})
        assert r.status_code == 201, r.text
        assert r.json()["role"] == "member", r.text
        if role != "member":
            set_role(r.json()["user_id"], role)
        r = client.post("/auth/token", data={"username": username, "password": "secret123"})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _make


@pytest.fixture
def admin(make_user) -> Dict[str, str]:
    return make_user(role="admin")


@pytest.fixture
def alice(make_user) -> Dict[str, str]:
    return make_user()


@pytest.fixture
def bob(make_user) -> Dict[str, str]:
    return make_user()


@pytest.fixture
def mock_http(monkeypatch) -> Callable[[Callable[[httpx.Request], httpx.Response]], list]:
    """Route every connector HTTP call to a handler; returns the list of requests seen."""
    from app.services.connectors import base as connector_base
    real_make_client = connector_base.make_client

    def install(handler):
        seen = []

        def wrapped(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        def make_client(allowed_hosts, **kwargs):
            # the production client (redirects, host guard) with only the network swapped out
            return real_make_client(allowed_hosts, transport=httpx.MockTransport(wrapped), **kwargs)
        monkeypatch.setattr(connector_base, "make_client", make_client)
        return seen
    return install


@pytest.fixture
def carol_headers(make_user) -> Dict[str, str]:
    return make_user()
