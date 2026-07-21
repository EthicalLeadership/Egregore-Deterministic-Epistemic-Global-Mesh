"""Tests for LocalModelClient discovery and memory checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from egregore.domain.inference_models import ChatMessage, ChatRequest, InferenceMode
from egregore.infrastructure.local_model_client import LocalModelClient


@pytest.fixture
def tiny_model_dir(tmp_path: Path) -> Path:
    """Create a minimal HuggingFace-format model directory."""
    model_dir = tmp_path / "tiny-local"
    model_dir.mkdir()
    config = {
        "architectures": ["TinyForCausalLM"],
        "model_type": "tiny",
        "torch_dtype": "bfloat16",
        "hidden_size": 128,
        "num_hidden_layers": 2,
        "vocab_size": 1000,
    }
    (model_dir / "config.json").write_text(json.dumps(config))
    (model_dir / "model.safetensors").write_bytes(b"x" * 1024)
    return model_dir


def test_discovers_model_from_directory(tiny_model_dir: Path) -> None:
    client = LocalModelClient(models_dir=tiny_model_dir.parent)
    assert client.health() is True
    assert client.model_exists("tiny-local") is True
    assert "tiny-local" in client.list_model_names()


def test_list_models_returns_metadata(tiny_model_dir: Path) -> None:
    client = LocalModelClient(models_dir=tiny_model_dir.parent)
    models = client.list_models()
    assert len(models) == 1
    entry = models[0]
    assert entry["name"] == "tiny-local"
    assert entry["architecture"] == "TinyForCausalLM"
    assert entry["torch_dtype"] == "bfloat16"
    # Directory size includes config.json plus the checkpoint file.
    assert entry["size_bytes"] >= 1024


def test_chat_fails_for_unknown_model(tiny_model_dir: Path) -> None:
    client = LocalModelClient(models_dir=tiny_model_dir.parent)
    request = ChatRequest(
        model="unknown-model",
        messages=[ChatMessage(role="user", content="hi")],
        mode=InferenceMode.DETERMINISTIC,
    )
    with pytest.raises(RuntimeError, match="not registered"):
        client.chat(request)


def test_chat_fails_when_model_exceeds_available_memory(
    tiny_model_dir: Path, monkeypatch
) -> None:
    client = LocalModelClient(models_dir=tiny_model_dir.parent)
    # Pretend the system has almost no available memory.
    monkeypatch.setattr(
        "egregore.infrastructure.local_model_client._available_system_memory_bytes",
        lambda: 1,
    )

    request = ChatRequest(
        model="tiny-local",
        messages=[ChatMessage(role="user", content="hi")],
        mode=InferenceMode.DETERMINISTIC,
    )
    with pytest.raises(RuntimeError, match="requires .* RAM"):
        client.chat(request)


def test_quantization_parsed_from_config(tmp_path: Path) -> None:
    model_dir = tmp_path / "quant-model"
    model_dir.mkdir()
    config = {
        "architectures": ["DeepseekV3ForCausalLM"],
        "model_type": "kimi_k2",
        "torch_dtype": "bfloat16",
        "hidden_size": 7168,
        "num_hidden_layers": 61,
        "vocab_size": 163840,
        "quantization_config": {
            "quant_method": "fp8",
            "fmt": "e4m3",
            "activation_scheme": "dynamic",
            "weight_block_size": [128, 128],
        },
    }
    (model_dir / "config.json").write_text(json.dumps(config))
    (model_dir / "model-1-of-1.safetensors").write_bytes(b"y" * 2048)

    client = LocalModelClient(models_dir=tmp_path)
    models = client.list_models()
    assert models[0]["quantization"] == "fp8/e4m3"
