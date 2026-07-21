"""Tests for AnthropicClient adapter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from egregore.domain.inference_models import ChatMessage, ChatRequest, InferenceMode
from egregore.infrastructure.anthropic_client import (
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicClient,
)


@pytest.fixture
def client() -> AnthropicClient:
    return AnthropicClient(api_key="test-key", base_url="http://localhost:9999")


def test_chat_maps_request_to_anthropic_format(client: AnthropicClient) -> None:
    request = ChatRequest(
        model="claude-3-haiku-20240307",
        messages=[
            ChatMessage(role="system", content="You are a tester."),
            ChatMessage(role="user", content="Hello."),
        ],
        mode=InferenceMode.DETERMINISTIC,
        max_tokens=100,
        seed=42,
    )

    mock_response = {
        "model": "claude-3-haiku-20240307",
        "content": [{"type": "text", "text": "Hi there."}],
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "stop_reason": "end_turn",
    }

    with patch.object(client, "_request", return_value=mock_response):
        response = client.chat(request)

    assert response.message.role == "assistant"
    assert response.message.content == "Hi there."
    assert response.model == "claude-3-haiku-20240307"
    assert response.usage["prompt_tokens"] == 10
    assert response.usage["completion_tokens"] == 2
    assert response.finish_reason == "end_turn"


def test_chat_aggregates_multiple_text_blocks(client: AnthropicClient) -> None:
    request = ChatRequest(
        model=DEFAULT_ANTHROPIC_MODEL,
        messages=[ChatMessage(role="user", content="Count to 2.")],
    )
    mock_response = {
        "model": DEFAULT_ANTHROPIC_MODEL,
        "content": [
            {"type": "text", "text": "One. "},
            {"type": "text", "text": "Two."},
        ],
        "usage": {"input_tokens": 3, "output_tokens": 4},
        "stop_reason": "end_turn",
    }

    with patch.object(client, "_request", return_value=mock_response):
        response = client.chat(request)

    assert response.message.content == "One. Two."


def test_generate_uses_chat_underneath(client: AnthropicClient) -> None:
    mock_response = {
        "model": DEFAULT_ANTHROPIC_MODEL,
        "content": [{"type": "text", "text": "42"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "stop_reason": "end_turn",
    }

    with patch.object(client, "_request", return_value=mock_response):
        text = client.generate("What is the answer?")

    assert text == "42"


def test_model_exists_for_known_claude_models(client: AnthropicClient) -> None:
    assert client.model_exists("claude-3-5-sonnet-20241022") is True
    assert client.model_exists("claude-3-opus-20240229") is True
    assert client.model_exists("claude-3-5-sonnet-latest") is True
    assert client.model_exists("llama3:instruct") is False


def test_health_false_without_api_key() -> None:
    no_key_client = AnthropicClient(api_key="")
    assert no_key_client.health() is False


def test_health_true_with_api_key(client: AnthropicClient) -> None:
    assert client.health() is True


def test_request_raises_when_no_key() -> None:
    no_key_client = AnthropicClient(api_key="")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        no_key_client._request({})
