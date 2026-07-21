"""Tests for InferenceService backend routing and governance."""

from __future__ import annotations

import pytest

from egregore.application.inference_service import InferenceService
from egregore.domain.inference_models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
)


class StubLlmClient:
    def __init__(self, name: str):
        self.name = name

    def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            message=ChatMessage(
                role="assistant", content=f"{self.name}:{request.model}"
            ),
            model=request.model,
            created_at_ns=1,
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            finish_reason="stop",
        )

    def generate(self, prompt: str, model: str | None = None) -> str:
        return f"{self.name}:generate"

    def list_models(self) -> list[dict[str, str]]:
        return [{"name": f"{self.name}-model"}]

    def health(self) -> bool:
        return True

    def pull_model(self, name: str) -> None:
        return None

    def delete_model(self, name: str) -> None:
        return None

    def model_exists(self, name: str) -> bool:
        return name == f"{self.name}-model"


def test_routes_claude_models_to_anthropic_backend() -> None:
    local_client = StubLlmClient("local")
    anthropic = StubLlmClient("anthropic")
    service = InferenceService(
        {"local": local_client, "anthropic": anthropic}, default_backend="local"
    )

    request = ChatRequest(
        model="claude-3-5-sonnet-20241022",
        messages=[ChatMessage(role="user", content="Hi")],
    )
    response = service.execute(request)

    assert response.message.content == "anthropic:claude-3-5-sonnet-20241022"


def test_routes_non_claude_models_to_default_backend() -> None:
    local_client = StubLlmClient("local")
    anthropic = StubLlmClient("anthropic")
    service = InferenceService(
        {"local": local_client, "anthropic": anthropic}, default_backend="local"
    )

    request = ChatRequest(
        model="llama3:instruct",
        messages=[ChatMessage(role="user", content="Hi")],
    )
    response = service.execute(request)

    assert response.message.content == "local:llama3:instruct"


def test_single_client_backwards_compatible() -> None:
    local_client = StubLlmClient("local")
    service = InferenceService(local_client, default_backend="local")

    request = ChatRequest(
        model="llama3:instruct",
        messages=[ChatMessage(role="user", content="Hi")],
    )
    response = service.execute(request)

    assert response.message.content == "local:llama3:instruct"


def test_model_exists_across_backends() -> None:
    local_client = StubLlmClient("local")
    anthropic = StubLlmClient("anthropic")
    service = InferenceService(
        {"local": local_client, "anthropic": anthropic}, default_backend="local"
    )

    assert service.model_exists("local-model") is True
    assert service.model_exists("anthropic-model") is True
    assert service.model_exists("missing") is False


def test_health_reports_all_backends() -> None:
    local_client = StubLlmClient("local")
    anthropic = StubLlmClient("anthropic")
    service = InferenceService(
        {"local": local_client, "anthropic": anthropic}, default_backend="local"
    )

    health = service.health()
    assert health["default_backend"] == "local"
    assert health["backends"]["local"]["reachable"] is True
    assert health["backends"]["anthropic"]["reachable"] is True


def test_missing_backend_raises_clear_error() -> None:
    service = InferenceService({}, default_backend="local")
    request = ChatRequest(
        model="claude-3-5-sonnet-20241022",
        messages=[ChatMessage(role="user", content="Hi")],
    )
    with pytest.raises(RuntimeError, match="Backend 'anthropic'"):
        service.execute(request)


def test_routes_deepseek_models_to_deepseek_backend() -> None:
    local_client = StubLlmClient("local")
    deepseek = StubLlmClient("deepseek")
    service = InferenceService(
        {"local": local_client, "deepseek": deepseek}, default_backend="local"
    )

    request = ChatRequest(
        model="deepseek-chat",
        messages=[ChatMessage(role="user", content="Hi")],
    )
    response = service.execute(request)

    assert response.message.content == "deepseek:deepseek-chat"


def test_model_exists_is_robust_to_unreachable_backends() -> None:
    local_client = StubLlmClient("local")
    deepseek = StubLlmClient("deepseek")

    def raises(_name: str) -> bool:
        raise ConnectionError("unreachable")

    local_client.model_exists = raises
    service = InferenceService(
        {"local": local_client, "deepseek": deepseek}, default_backend="local"
    )

    assert service.model_exists("deepseek-model") is True
    assert service.model_exists("missing") is False


def test_routes_kimi_models_to_local_backend() -> None:
    local_client = StubLlmClient("local")
    local2 = StubLlmClient("local")
    service = InferenceService(
        {"local": local_client, "local2": local2}, default_backend="local"
    )

    request = ChatRequest(
        model="kimi-k2-base",
        messages=[ChatMessage(role="user", content="Hi")],
    )
    response = service.execute(request)

    assert response.message.content == "local:kimi-k2-base"


def test_default_backend_is_configurable() -> None:
    local = StubLlmClient("local")
    service = InferenceService({"local": local}, default_backend="local")

    request = ChatRequest(
        model="some-model",
        messages=[ChatMessage(role="user", content="Hi")],
    )
    response = service.execute(request)

    assert response.message.content == "local:some-model"
    assert service.health()["default_backend"] == "local"
