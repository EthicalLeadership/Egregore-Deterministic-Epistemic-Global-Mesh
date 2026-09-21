"""Tests for /v1/chat multi-backend router."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from egregore.application.inference_service import InferenceService
from egregore.domain.inference_models import ChatMessage, ChatResponse
from egregore.http_api.http.app import create_app

VALID_KEY = "a" * 64


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch):
    monkeypatch.setenv("EGREGORE_API_KEYS", f"{VALID_KEY}:default:user:admin")


@pytest.fixture
def client_with_local():
    from egregore.http_api.http.middleware import api_key_middleware

    # Hot-reload keys for this test
    api_key_middleware._API_KEYS = {VALID_KEY: ("default", "user", "admin")}

    mock_local = MagicMock()
    mock_local.health.return_value = True
    mock_local.list_models.return_value = [{"name": "llama3:instruct"}]
    mock_local.model_exists.return_value = True
    mock_local.chat.return_value = ChatResponse(
        message=ChatMessage(role="assistant", content="Hello from local backend"),
        model="llama3:instruct",
        created_at_ns=1,
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        finish_reason="stop",
    )

    app = create_app(build_container=False)
    app.state.inference_service = InferenceService(mock_local, default_backend="local")
    return TestClient(app)


def test_chat_completions_uses_local_backend(client_with_local):
    payload = {
        "model": "llama3:instruct",
        "messages": [{"role": "user", "content": "Hi"}],
    }
    r = client_with_local.post(
        "/v1/chat/completions", json=payload, headers={"X-API-Key": VALID_KEY}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["message"]["content"] == "Hello from local backend"
    assert data["governance"]["m1_projection_access"] is True


def test_chat_completions_routes_claude_to_anthropic(monkeypatch):
    from egregore.http_api.http.middleware import api_key_middleware

    api_key_middleware._API_KEYS = {VALID_KEY: ("default", "user", "admin")}

    mock_anthropic = MagicMock()
    mock_anthropic.health.return_value = True
    mock_anthropic.list_models.return_value = [{"name": "claude-3-5-sonnet-20241022"}]
    mock_anthropic.model_exists.return_value = True
    mock_anthropic.chat.return_value = ChatResponse(
        message=ChatMessage(role="assistant", content="Hello from Claude"),
        model="claude-3-5-sonnet-20241022",
        created_at_ns=1,
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        finish_reason="stop",
    )

    app = create_app(build_container=False)
    app.state.inference_service = InferenceService(
        {"anthropic": mock_anthropic}, default_backend="anthropic"
    )
    client = TestClient(app)

    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hi"}],
    }
    r = client.post(
        "/v1/chat/completions", json=payload, headers={"X-API-Key": VALID_KEY}
    )
    assert r.status_code == 200
    assert r.json()["message"]["content"] == "Hello from Claude"


def test_list_models_aggregates_backends(client_with_local):
    r = client_with_local.get("/v1/models", headers={"X-API-Key": VALID_KEY})
    assert r.status_code == 200
    assert any(m.get("name") == "llama3:instruct" for m in r.json())


def test_no_backend_available_returns_503():
    from egregore.http_api.http.middleware import api_key_middleware

    api_key_middleware._API_KEYS = {VALID_KEY: ("default", "user", "admin")}

    mock_local = MagicMock()
    mock_local.health.return_value = False
    mock_local.list_models.return_value = []

    app = create_app(build_container=False)
    app.state.inference_service = InferenceService(mock_local, default_backend="local")
    client = TestClient(app)

    payload = {
        "model": "llama3:instruct",
        "messages": [{"role": "user", "content": "Hi"}],
    }
    r = client.post(
        "/v1/chat/completions", json=payload, headers={"X-API-Key": VALID_KEY}
    )
    assert r.status_code == 503
