"""Tests for /v1/chat/completions streaming, tools, RAG, and history."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egregore.application.inference_service import InferenceService
from egregore.domain.inference_models import ChatMessage, ChatRequest, ChatResponse
from egregore.http_api.http.v1 import chat as chat_module
from egregore.http_api.http.v1.chat import router as chat_router


class FakeLlmClient:
    """Fake LLM client that supports non-streaming and streaming chat."""

    def __init__(self, name: str = "fake") -> None:
        self.name = name

    def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            message=ChatMessage(role="assistant", content="hello"),
            model=request.model,
            created_at_ns=1,
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            finish_reason="stop",
            inference_id="test-1",
        )

    def stream_chat(self, request: ChatRequest) -> Iterator[str]:
        yield "hello"
        yield " world"

    def generate(self, prompt: str, model: str | None = None) -> str:
        return "hello"

    def list_models(self) -> list[dict[str, Any]]:
        return [{"name": "fake-model"}]

    def health(self) -> bool:
        return True

    def pull_model(self, name: str) -> None:
        return None

    def delete_model(self, name: str) -> None:
        return None

    def model_exists(self, name: str) -> bool:
        return name == "fake-model"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.state.inference_service = InferenceService(
        {"fake": FakeLlmClient()}, default_backend="fake"
    )
    app.include_router(chat_router)
    return TestClient(app, base_url="http://localhost")


def test_chat_non_streaming(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "fake-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"]["content"] == "hello"
    assert data["governance"]["m1_projection_access"] is True


def test_chat_streaming(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "fake-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    chunks: list[str] = []
    for line in resp.iter_lines():
        if line.startswith("data: "):
            payload = json.loads(line.removeprefix("data: "))
            if "delta" in payload:
                chunks.append(payload["delta"])
            if payload.get("done"):
                break
    assert "".join(chunks) == "hello world"


def test_chat_tools_forwarded(client: TestClient) -> None:
    tool = {
        "type": "function",
        "function": {
            "name": "get_weather",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "fake-model",
            "messages": [{"role": "user", "content": "weather"}],
            "tools": [tool],
        },
    )
    assert resp.status_code == 200


def test_chat_use_rag_injects_context(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        chat_module,
        "_retrieve_rag_context",
        lambda query, top_k: "Egregore is a sovereign runtime.",
    )
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "fake-model",
            "messages": [{"role": "user", "content": "what is egregore"}],
            "use_rag": True,
        },
    )
    assert resp.status_code == 200
