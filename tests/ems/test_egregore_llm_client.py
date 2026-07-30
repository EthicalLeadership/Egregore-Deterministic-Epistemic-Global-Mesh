"""Tests for EgregoreLlmClient — the ILlmClient adapter for EMS."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from egregore.domain.inference_models import ChatMessage, ChatRequest, InferenceMode
from egregore.infrastructure.egregore_llm_client import EgregoreLlmClient


class TestEgregoreLlmClient:
    def test_request_payload_deterministic(self):
        client = EgregoreLlmClient(base_url="http://test")
        request = ChatRequest(
            model="coder-ft-v1",
            messages=[
                ChatMessage(role="system", content="You are a coder."),
                ChatMessage(role="user", content="Hello"),
            ],
            mode=InferenceMode.DETERMINISTIC,
            max_tokens=128,
            seed=42,
        )
        payload = client._request_payload(request)
        assert payload["model"] == "coder-ft-v1"
        assert payload["temperature"] == 0.0
        assert payload["top_p"] == 1.0
        assert payload["seed"] == 42
        assert len(payload["messages"]) == 2

    def test_request_payload_creative(self):
        client = EgregoreLlmClient(base_url="http://test")
        request = ChatRequest(
            model="architect-ft-v1",
            messages=[ChatMessage(role="user", content="Hi")],
            mode=InferenceMode.CREATIVE,
            max_tokens=256,
            seed=None,
        )
        payload = client._request_payload(request)
        assert payload["temperature"] == 0.7
        assert "seed" not in payload

    def test_generate(self):
        client = EgregoreLlmClient(base_url="http://test")
        with patch.object(client, "chat") as mock_chat:
            mock_chat.return_value = MagicMock(
                message=MagicMock(content="Generated text")
            )
            result = client.generate("Tell me a joke", model="coder-ft-v1")
            assert result == "Generated text"
            mock_chat.assert_called_once()
            call_args = mock_chat.call_args[0][0]
            assert call_args.model == "coder-ft-v1"

    def test_model_exists_true(self):
        client = EgregoreLlmClient(base_url="http://test")
        with patch.object(client, "list_models") as mock_list:
            mock_list.return_value = [{"id": "coder-ft-v1"}, {"id": "architect-ft-v1"}]
            assert client.model_exists("coder-ft-v1") is True
            assert client.model_exists("missing") is False

    def test_pull_delete_not_implemented(self):
        client = EgregoreLlmClient(base_url="http://test")
        with pytest.raises(NotImplementedError):
            client.pull_model("anything")
        with pytest.raises(NotImplementedError):
            client.delete_model("anything")
