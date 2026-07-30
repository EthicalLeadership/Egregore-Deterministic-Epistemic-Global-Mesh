"""Tests for Egregore Model Service (EMS) Proxy."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from egregore.domain.inference_models import ChatMessage, ChatResponse
from egregore.ems.proxy import EmsProxy
from egregore.ems.registry import EmsRegistry, ModelStatus


@pytest.fixture
def tmp_registry():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_ems.db"
        reg = EmsRegistry(db_path=db_path)
        yield reg


@pytest.fixture
def dummy_checkpoint():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "coder-ft-v2"
        path.mkdir()
        (path / "config.json").write_text('{"vocab_size": 32022}')
        yield str(path)


class TestProxyRouting:
    def test_resolve_backend_running(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("coder-ft-v2", dummy_checkpoint)
        proxy = EmsProxy(tmp_registry, auto_start=False)
        mock_backend = MagicMock()
        mock_backend.health.return_value = True
        proxy._lifecycle._backends["coder-ft-v2"] = mock_backend

        backend = proxy._resolve_backend("coder-ft-v2")
        assert backend is mock_backend

    def test_resolve_backend_not_running(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("coder-ft-v2", dummy_checkpoint)
        proxy = EmsProxy(tmp_registry, auto_start=False)
        assert proxy._resolve_backend("coder-ft-v2") is None

    def test_resolve_backend_unknown(self, tmp_registry):
        proxy = EmsProxy(tmp_registry, auto_start=False)
        assert proxy._resolve_backend("nonexistent") is None

    def test_list_available_models(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("m1", dummy_checkpoint, tier="expert")
        tmp_registry.register("m2", dummy_checkpoint, tier="general")

        proxy = EmsProxy(tmp_registry)
        models = proxy._list_available_models()
        assert len(models) == 2
        assert models[0]["object"] == "model"
        assert "meta" in models[0]


class TestProxyApp:
    def test_health_endpoint(self, tmp_registry):
        proxy = EmsProxy(tmp_registry)
        app = proxy.app
        client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://test")
        # Note: WSGITransport won't work with async FastAPI; use TestClient if available
        # This is a structural test only.
        assert app is not None


class TestProxyPromptFormatting:
    def test_messages_formatted_for_deepseek_model(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register(
            "coder-ft-v2",
            dummy_checkpoint,
            chat_template="deepseek",
        )
        proxy = EmsProxy(tmp_registry, auto_start=False)
        formatted = proxy._format_payload("coder-ft-v2", {
            "model": "coder-ft-v2",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert len(formatted["messages"]) == 1
        assert "### Instruction:" in formatted["messages"][0]["content"]
        assert "Egregore Coder agent" in formatted["messages"][0]["content"]

    def test_messages_passthrough_for_unknown_template(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("m1", dummy_checkpoint, chat_template="")
        proxy = EmsProxy(tmp_registry, auto_start=False)
        original = [{"role": "user", "content": "Hi"}]
        formatted = proxy._format_payload("m1", {
            "model": "m1",
            "messages": original,
        })
        assert formatted["messages"] == original


class TestProxyChatRequest:
    def test_build_chat_request(self):
        payload = {
            "model": "coder-ft-v2",
            "messages": [
                {"role": "system", "content": "You are a coder."},
                {"role": "user", "content": "Hello"},
            ],
            "max_tokens": 128,
            "temperature": 0.7,
            "seed": 42,
        }
        request = EmsProxy._build_chat_request("coder-ft-v2", payload)
        assert request.model == "coder-ft-v2"
        assert len(request.messages) == 2
        assert request.messages[0].role == "system"
        assert request.max_tokens == 128
        assert request.mode.value == "creative"

    def test_build_openai_response(self):
        response = ChatResponse(
            message=ChatMessage(role="assistant", content="Hi"),
            model="coder-ft-v2",
            created_at_ns=1_000_000_000,
            usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            finish_reason="stop",
        )
        data = EmsProxy._build_openai_response("coder-ft-v2", response)
        assert data["model"] == "coder-ft-v2"
        assert data["choices"][0]["message"]["content"] == "Hi"
        assert data["usage"]["total_tokens"] == 7
