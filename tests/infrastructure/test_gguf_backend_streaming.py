"""Tests for GgufBackend streaming and dispatch-path validation."""

from __future__ import annotations

import pytest

from egregore.application.inference_service import InferenceService
from egregore.domain.inference_models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    InferenceMode,
)
from egregore.infrastructure import gguf_backend
from egregore.infrastructure.gguf_backend import GgufBackend, _parse_models_env


def _request(model: str = "test-model") -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=[ChatMessage(role="user", content="hi")],
        mode=InferenceMode.DETERMINISTIC,
        max_tokens=8,
        seed=1,
    )


class _FakeLlama:
    """Minimal llama-cpp-python stand-in."""

    def create_chat_completion(self, **kwargs):
        if kwargs.get("stream"):
            return iter(
                [
                    {"choices": [{"delta": {"role": "assistant"}}]},
                    {"choices": [{"delta": {"content": "tok1"}}]},
                    {"choices": [{"delta": {"content": "tok2"}}]},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ]
            )
        return {
            "choices": [
                {"message": {"content": "full text"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }


@pytest.fixture
def backend():
    b = GgufBackend(models={"test-model": "/models/gguf/general/fake.gguf"}, n_ctx=64)
    b._instances["test-model"] = _FakeLlama()
    return b


def test_stream_chat_yields_content_deltas(backend):
    chunks = list(backend.stream_chat(_request()))
    assert chunks == ["tok1", "tok2"]


def test_stream_chat_strips_gguf_prefix(backend):
    chunks = list(backend.stream_chat(_request("gguf-test-model")))
    assert chunks == ["tok1", "tok2"]


def test_stream_chat_unknown_model_raises(backend):
    with pytest.raises(RuntimeError, match="no model"):
        list(backend.stream_chat(_request("nope")))


def test_chat_still_works_after_prepare_refactor(backend):
    response = backend.chat(_request())
    assert response.message.content == "full text"
    assert response.finish_reason == "stop"


def test_parse_models_env_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("EGREGORE_MODELS_ROOT", str(tmp_path))
    monkeypatch.setenv("EGREGORE_GGUF_MODELS", "evil=/etc/passwd")
    with pytest.raises(ValueError, match="escapes allowed root"):
        _parse_models_env()


def test_parse_models_env_accepts_paths_inside_root(tmp_path, monkeypatch):
    model = tmp_path / "gguf" / "general" / "ok.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"x")
    monkeypatch.setenv("EGREGORE_MODELS_ROOT", str(tmp_path))
    monkeypatch.setenv("EGREGORE_GGUF_MODELS", f"ok={model}")
    parsed = _parse_models_env()
    assert parsed == {"ok": str(model.resolve())}


def test_parse_models_env_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("EGREGORE_GGUF_MODELS", raising=False)
    parsed = _parse_models_env()
    assert parsed == dict(gguf_backend._DEFAULT_MODELS)
    # Stale-path regression: defaults must point at real fleet filenames.
    assert "my_coder_ft_fixed-Q4_K_M.gguf" in parsed["my-coder-ft"]


class _StreamClient:
    def chat(self, request):
        return ChatResponse(
            message=ChatMessage(role="assistant", content="full"),
            model=request.model,
            created_at_ns=0,
            usage={},
            finish_reason="stop",
        )

    def stream_chat(self, request):
        yield "a"
        yield "b"


def test_execute_stream_delegates_to_backend():
    service = InferenceService({"egregore": _StreamClient()})
    assert list(service.execute_stream(_request("coder-x"))) == ["a", "b"]


def test_execute_stream_falls_back_to_single_chunk():
    class Plain:
        def chat(self, request):
            return ChatResponse(
                message=ChatMessage(role="assistant", content="full"),
                model=request.model,
                created_at_ns=0,
                usage={},
                finish_reason="stop",
            )

    service = InferenceService({"egregore": Plain()})
    assert list(service.execute_stream(_request("coder-x"))) == ["full"]


def test_execute_stream_unknown_backend_raises():
    service = InferenceService({"egregore": _StreamClient()})
    with pytest.raises(RuntimeError, match="not registered"):
        list(service.execute_stream(_request("gguf-missing")))
