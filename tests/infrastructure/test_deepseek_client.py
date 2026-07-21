"""Tests for DeepSeekClient adapter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from egregore.domain.inference_models import ChatMessage, ChatRequest, InferenceMode
from egregore.infrastructure.deepseek_client import (
    DEFAULT_DEEPSEEK_MODEL,
    DeepSeekClient,
)


@pytest.fixture
def client() -> DeepSeekClient:
    return DeepSeekClient(api_key="test-key", base_url="http://localhost:9999")


def test_chat_maps_request_to_deepseek_format(client: DeepSeekClient) -> None:
    request = ChatRequest(
        model="deepseek-chat",
        messages=[
            ChatMessage(role="system", content="You are a tester."),
            ChatMessage(role="user", content="Hello."),
        ],
        mode=InferenceMode.DETERMINISTIC,
        max_tokens=100,
        seed=42,
    )

    mock_response = {
        "model": "deepseek-chat",
        "choices": [
            {
                "message": {"role": "assistant", "content": "Hi there."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }

    with patch.object(client, "_request", return_value=mock_response):
        response = client.chat(request)

    assert response.message.role == "assistant"
    assert response.message.content == "Hi there."
    assert response.model == "deepseek-chat"
    assert response.usage["prompt_tokens"] == 10
    assert response.usage["completion_tokens"] == 2
    assert response.finish_reason == "stop"


def test_generate_uses_chat_underneath(client: DeepSeekClient) -> None:
    mock_response = {
        "model": DEFAULT_DEEPSEEK_MODEL,
        "choices": [
            {"message": {"role": "assistant", "content": "42"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    with patch.object(client, "_request", return_value=mock_response):
        text = client.generate("What is the answer?")

    assert text == "42"


def test_model_exists_for_known_deepseek_models(client: DeepSeekClient) -> None:
    assert client.model_exists("deepseek-chat") is True
    assert client.model_exists("deepseek-reasoner") is True
    assert client.model_exists("deepseek-chat-v2") is True
    assert client.model_exists("llama3:instruct") is False


def test_health_false_without_api_key() -> None:
    no_key_client = DeepSeekClient(api_key="")
    assert no_key_client.health() is False


def test_health_true_with_api_key(client: DeepSeekClient) -> None:
    assert client.health() is True


def test_request_raises_when_no_key() -> None:
    no_key_client = DeepSeekClient(api_key="")
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        no_key_client._request({})
