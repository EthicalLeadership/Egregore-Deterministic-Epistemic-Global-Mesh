"""Tests for /v1/code endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from egregore.application.code_factory import CodeFactoryService
from egregore.application.inference_service import InferenceService
from egregore.domain.inference_models import ChatMessage, ChatResponse
from egregore.http_api.http.app import create_app

VALID_KEY = "a" * 64


@pytest.fixture
def client_with_factory():
    from egregore.http_api.http.middleware import api_key_middleware

    api_key_middleware._API_KEYS = {VALID_KEY: ("default", "user", "admin")}

    stub_client = MagicMock()
    stub_client.chat.return_value = ChatResponse(
        message=ChatMessage(role="assistant", content="def generated(): pass"),
        model="claude-3-5-sonnet-20241022",
        created_at_ns=1,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        finish_reason="stop",
    )
    stub_client.health.return_value = True
    stub_client.list_models.return_value = []

    inference = InferenceService(
        {"anthropic": stub_client}, default_backend="anthropic"
    )
    app = create_app(build_container=False)
    app.state.inference_service = inference
    app.state.code_factory = CodeFactoryService(inference)
    return TestClient(app)


def test_code_generate_endpoint(client_with_factory):
    payload = {
        "task_type": "generate",
        "prompt": "write a function that adds two numbers",
        "language": "python",
    }
    r = client_with_factory.post(
        "/v1/code", json=payload, headers={"X-API-Key": VALID_KEY}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_type"] == "generate"
    assert data["language"] == "python"
    assert data["content"] == "def generated(): pass"
    assert data["governance"]["m1_projection_access"] is True


def test_invalid_task_type_is_rejected(client_with_factory):
    payload = {
        "task_type": "invalid",
        "prompt": "do something",
    }
    r = client_with_factory.post(
        "/v1/code", json=payload, headers={"X-API-Key": VALID_KEY}
    )
    assert r.status_code == 422


def test_code_health_endpoint(client_with_factory):
    r = client_with_factory.get("/v1/code/health", headers={"X-API-Key": VALID_KEY})
    assert r.status_code == 200
    data = r.json()
    assert data["default_code_model"] == "claude-3-5-sonnet-20241022"
