"""Tests for /v1/orchestrate — manifest-driven model auto-selection endpoint."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from egregore.application.inference_service import InferenceService
from egregore.domain.inference_models import ChatMessage, ChatResponse
from egregore.http_api.http.app import create_app
from egregore.http_api.http.middleware import api_key_middleware
from egregore.http_api.http.v1 import orchestrate as orchestrate_mod

VALID_KEY = "a" * 64

PROFILES = {
    "model-a": {
        "catalog_key": "specialized/a-Q4_K_M",
        "routes_to": "a",
        "task_tags": ["code"],
        "quality_score": 80,
    },
    "model-b": {
        "catalog_key": "expert/b-Q4_K_M",
        "routes_to": "b",
        "task_tags": ["general", "legal"],
        "quality_score": 85,
    },
}


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    monkeypatch.setenv("EGREGORE_API_KEYS", f"{VALID_KEY}:default:user:admin")
    api_key_middleware._API_KEYS = {VALID_KEY: ("default", "user", "admin")}
    _profiles_holder["profiles"] = PROFILES
    orchestrate_mod._reset_cache()
    yield
    orchestrate_mod._reset_cache()


class _FakeBackend:
    def __init__(self):
        self.seen_models: list[str] = []

    def health(self):
        return True

    def list_models(self):
        return [{"id": "a"}, {"id": "b"}]

    def model_exists(self, name):
        return True

    def chat(self, request):
        self.seen_models.append(request.model)
        return ChatResponse(
            message=ChatMessage(role="assistant", content=f"ok via {request.model}"),
            model=request.model,
            created_at_ns=1,
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            finish_reason="stop",
        )

    def stream_chat(self, request):
        yield "chunk1"
        yield "chunk2"


TEST_CATALOG = {
    "specialized/a-Q4_K_M": {"size_bytes": 1000},
    "expert/b-Q4_K_M": {"size_bytes": 2000},
}

# Mutable holder: the patched load_profiles reads through it, so tests can
# swap profiles even after create_app re-imported (and froze) the router's
# from-import binding.
_profiles_holder: dict = {"profiles": PROFILES}


def _patch_selector_sources(monkeypatch, profiles):
    """Patch at the source modules so the patch survives create_app's
    sys.modules purge + fresh re-import of v1 routers.
    """
    _profiles_holder["profiles"] = profiles
    monkeypatch.setattr(
        "egregore.application.model_selector.load_profiles",
        lambda path=None: _profiles_holder["profiles"],
    )
    from egregore.infrastructure.gguf_catalog import GGUFCatalog

    monkeypatch.setattr(GGUFCatalog, "get_catalog", lambda self: TEST_CATALOG)


@pytest.fixture
def client(monkeypatch):
    backend = _FakeBackend()
    _patch_selector_sources(monkeypatch, PROFILES)
    app = create_app(build_container=False)
    app.state.inference_service = InferenceService(
        {"gguf": backend, "egregore": backend}
    )
    tc = TestClient(app)
    tc.backend = backend  # type: ignore[attr-defined]
    return tc


def _post(client, payload):
    return client.post(
        "/v1/orchestrate/chat/completions",
        json=payload,
        headers={"X-API-Key": VALID_KEY},
    )


def test_auto_selects_model_for_task(client):
    r = _post(
        client,
        {"task_type": "legal", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["resolution"]["selected_model"] == "b"
    assert data["resolution"]["fallback_used"] is False
    # Routed through the gguf backend with the routing prefix.
    assert client.backend.seen_models == ["gguf-b"]


def test_invalid_task_type_is_400(client):
    r = _post(
        client,
        {"task_type": "bogus", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400
    assert "Invalid task_type" in r.json()["detail"]


def test_explicit_model_bypasses_selector(client):
    r = _post(
        client,
        {
            "model": "my-explicit",
            "task_type": "bogus-ignored",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["resolution"]["selected_model"] == "my-explicit"
    assert client.backend.seen_models == ["my-explicit"]


def test_fallback_when_task_not_tagged(client, monkeypatch):
    profiles = {k: dict(v, task_tags=["code"]) for k, v in PROFILES.items()}
    _profiles_holder["profiles"] = profiles
    r = _post(
        client,
        {"task_type": "legal", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    resolution = r.json()["resolution"]
    assert resolution["fallback_used"] is True
    assert resolution["selected_model"] == "b"  # highest quality overall


def test_no_model_available_is_503(client, monkeypatch):
    from egregore.infrastructure.gguf_catalog import GGUFCatalog

    monkeypatch.setattr(GGUFCatalog, "get_catalog", lambda self: {})
    orchestrate_mod._reset_cache()
    r = _post(
        client,
        {"task_type": "legal", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 503


def test_disclosure_flag_controls_internal_details(client, monkeypatch):
    payload = {"task_type": "code", "messages": [{"role": "user", "content": "hi"}]}

    monkeypatch.setenv("ORCHESTRATE_DISCLOSE_INTERNAL", "false")
    hidden = _post(client, payload).json()["resolution"]
    assert "catalog_key" not in hidden
    assert "logical_id" not in hidden

    monkeypatch.setenv("ORCHESTRATE_DISCLOSE_INTERNAL", "true")
    shown = _post(client, payload).json()["resolution"]
    assert shown["catalog_key"] == "specialized/a-Q4_K_M"
    assert shown["logical_id"] == "model-a"


def test_streaming_sse_shape(client):
    with client.stream(
        "POST",
        "/v1/orchestrate/chat/completions",
        json={
            "task_type": "code",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
        headers={"X-API-Key": VALID_KEY},
    ) as r:
        assert r.status_code == 200
        frames = [
            json.loads(line.removeprefix("data: "))
            for line in r.iter_lines()
            if line.startswith("data: ")
        ]
    assert "resolution" in frames[0]
    deltas = [f["delta"] for f in frames if "delta" in f]
    assert deltas == ["chunk1", "chunk2"]
    assert frames[-1] == {"done": True}
