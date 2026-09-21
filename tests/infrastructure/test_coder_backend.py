"""Smoke tests for the native CoderBackend."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from egregore.domain.inference_models import ChatMessage, ChatRequest, InferenceMode
from egregore.infrastructure.coder_backend import CoderBackend


class _FakeTokenizer:
    """Minimal tokenizer stand-in for mocked tests."""

    pad_token_id = 0
    eos_token_id = 1

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        return "### Instruction:\nhello\n### Response:\n"

    def __call__(self, prompt, return_tensors=None):
        return BatchEncoding({"input_ids": [[10, 11, 12]]})

    def decode(self, tokens, skip_special_tokens=True):
        return "Hi!"


class FakeTensor:
    """Minimal tensor stand-in."""

    def __init__(self, data):
        self._data = data
        self.shape = (len(data), len(data[0]) if data else 0)


class BatchEncoding(dict):
    """Minimal dict-like return value for fake tokenizer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key, value in self.items():
            setattr(self, key, FakeTensor(value))

    def to(self, device):
        return self

class _FakeModel:
    device = "cpu"

    def eval(self):
        pass

    def generate(self, **kwargs):
        class _Out:
            def __getitem__(self, idx):
                return [10, 11, 12, 20, 21]

        return _Out()


def test_backend_disabled_when_env_var_set():
    backend = CoderBackend(model_path="/nonexistent", enabled=False)
    assert backend.health() is False
    assert backend.list_models() == []
    with pytest.raises(RuntimeError, match="not healthy"):
        backend.chat(
            ChatRequest(model="my-coder-ft", messages=[ChatMessage(role="user", content="hi")])
        )


def test_backend_reports_unhealthy_when_model_path_missing():
    backend = CoderBackend(model_path="/does/not/exist", enabled=True)
    assert backend.health() is False
    assert backend.model_exists("my-coder-ft") is False


@patch("transformers.AutoTokenizer")
@patch("transformers.AutoModelForCausalLM")
@patch("transformers.BitsAndBytesConfig")
def test_backend_runs_chat_against_mocked_model(_bnb, mock_model_cls, mock_tokenizer_cls, tmp_path):
    # Create a dummy directory so the existence check passes.
    fake_model_dir = tmp_path / "fake_model"
    fake_model_dir.mkdir()

    mock_tokenizer_cls.from_pretrained.return_value = _FakeTokenizer()
    mock_model_cls.from_pretrained.return_value = _FakeModel()

    backend = CoderBackend(model_path=str(fake_model_dir), enabled=True)
    assert backend.health() is True
    assert backend.model_exists("my-coder-ft") is True
    models = backend.list_models()
    assert len(models) == 1
    assert models[0]["name"] == "my-coder-ft"
    assert models[0]["backend"] == "egregore"

    response = backend.chat(
        ChatRequest(
            model="my-coder-ft",
            messages=[ChatMessage(role="user", content="hello")],
            max_tokens=10,
            mode=InferenceMode.DETERMINISTIC,
        )
    )
    assert response.message.role == "assistant"
    assert response.message.content == "Hi!"
    assert response.model == "my-coder-ft"
    assert response.usage["prompt_tokens"] > 0
    assert response.usage["completion_tokens"] > 0


@pytest.mark.skipif(
    os.environ.get("EGREGORE_CODER_RUN_LIVE_TEST") != "1",
    reason="Set EGREGORE_CODER_RUN_LIVE_TEST=1 to run the live GPU model test",
)
def test_live_backend_loads_and_generates():
    """Live smoke test against the real model. Requires GPU and the model directory."""
    backend = CoderBackend()
    assert backend.health() is True

    response = backend.chat(
        ChatRequest(
            model="my-coder-ft",
            messages=[ChatMessage(role="user", content="Say hello")],
            max_tokens=16,
        )
    )
    assert response.message.content.strip()
    assert response.usage["total_tokens"] > 0
