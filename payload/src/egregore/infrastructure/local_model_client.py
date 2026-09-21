"""Local HuggingFace-format model backend.

Discovers safetensors/checkpoint directories under a configured root and serves
them through the ILlmClient protocol. Inference is performed lazily via
transformers/torch only when the model fits in available system memory.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from egregore.domain.inference_models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    InferenceMode,
)
from egregore.shared.canonical import canonical_loads

DEFAULT_LOCAL_MODELS_DIR = os.environ.get(
    "EXTERNAL_MODELS_DIR", "/opt/egregore/external_models"
)
MEMORY_OVERHEAD_FACTOR = 1.15  # KV cache, activations, OS overhead buffer


def _local_models_dir() -> Path:
    """Resolve configured local models directory."""
    env_dir = os.environ.get("EGREGORE_LOCAL_MODELS_DIR", "")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return Path(DEFAULT_LOCAL_MODELS_DIR).resolve()


@dataclass(frozen=True)
class LocalModelInfo:
    """Metadata for a discovered local model."""

    model_id: str
    path: Path
    architecture: str
    torch_dtype: str
    hidden_size: int
    num_hidden_layers: int
    vocab_size: int
    total_size_bytes: int
    quantization: str | None

    @property
    def estimated_ram_bytes(self) -> int:
        """Conservative RAM estimate including overhead."""
        return int(self.total_size_bytes * MEMORY_OVERHEAD_FACTOR)

    def ram_requirement_text(self) -> str:
        """Human-readable RAM requirement."""
        gb = self.estimated_ram_bytes / (1024**3)
        return f"~{gb:.1f} GB"


def _total_directory_size(path: Path) -> int:
    """Sum byte sizes of all regular files under path."""
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            with contextlib.suppress(OSError):
                total += entry.stat().st_size
    return total


def _parse_quantization(config: dict[str, Any]) -> str | None:
    """Extract quantization description from config.json."""
    q = config.get("quantization_config")
    if isinstance(q, dict):
        method = q.get("quant_method")
        fmt = q.get("fmt")
        if method and fmt:
            return f"{method}/{fmt}"
        return method or fmt
    return None


def _discover_models(models_dir: Path) -> dict[str, LocalModelInfo]:
    """Scan models_dir for HuggingFace-format checkpoint directories."""
    models: dict[str, LocalModelInfo] = {}
    if not models_dir.exists():
        return models

    for entry in models_dir.iterdir():
        if not entry.is_dir():
            continue
        config_path = entry / "config.json"
        if not config_path.exists():
            continue
        try:
            config = canonical_loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        architectures = config.get("architectures", [])
        architecture = (
            architectures[0] if architectures else config.get("model_type", "unknown")
        )
        model_id = entry.name
        total_size = _total_directory_size(entry)

        # Skip empty or malformed directories.
        if total_size == 0:
            continue

        models[model_id] = LocalModelInfo(
            model_id=model_id,
            path=entry,
            architecture=architecture,
            torch_dtype=config.get("torch_dtype", "unknown"),
            hidden_size=config.get("hidden_size", 0),
            num_hidden_layers=config.get("num_hidden_layers", 0),
            vocab_size=config.get("vocab_size", 0),
            total_size_bytes=total_size,
            quantization=_parse_quantization(config),
        )

    return models


def _available_system_memory_bytes() -> int:
    """Return available physical RAM in bytes."""
    try:
        import psutil

        return psutil.virtual_memory().available
    except Exception:
        # Fallback: read /proc/meminfo on Linux.
        with contextlib.suppress(Exception), open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb * 1024
    return 0


class LocalModelClient:
    """ILlmClient backend for locally stored HuggingFace-format models."""

    def __init__(self, models_dir: Path | str | None = None):
        self.models_dir = (
            Path(models_dir).expanduser().resolve()
            if models_dir
            else _local_models_dir()
        )
        self._models = _discover_models(self.models_dir)

    # ------------------------------------------------------------------
    # ILlmClient protocol
    # ------------------------------------------------------------------
    def chat(self, request: ChatRequest) -> ChatResponse:
        """Run chat inference on a local model.

        Raises:
            RuntimeError: if the model is unknown, does not fit in memory,
            or the required inference libraries are not installed.

        """
        model_id = request.model
        info = self._models.get(model_id)
        if info is None:
            raise RuntimeError(
                f"Local model '{model_id}' is not registered. "
                f"Discovered models: {sorted(self._models)}"
            )

        available = _available_system_memory_bytes()
        if available < info.estimated_ram_bytes:
            raise RuntimeError(
                f"Local model '{model_id}' requires {info.ram_requirement_text()} of available RAM, "
                f"but this system only has {available / (1024**3):.1f} GB available. "
                "Install a quantized/GGUF version or add more RAM."
            )

        return self._run_transformers_chat(request, info)

    def generate(self, prompt: str, model: str | None = None) -> str:
        """Single-turn text generation."""
        target_model = model or os.environ.get("EGREGORE_CHAT_MODEL", "")
        if not target_model:
            raise RuntimeError("No model specified for local generation.")

        request = ChatRequest(
            model=target_model,
            messages=[
                ChatMessage(role="system", content="You are a helpful assistant."),
                ChatMessage(role="user", content=prompt),
            ],
            mode=InferenceMode.DETERMINISTIC,
            max_tokens=2048,
        )
        response = self.chat(request)
        return response.message.content

    def list_models(self) -> list[dict[str, Any]]:
        """Return discovered local models with metadata."""
        return [
            {
                "name": info.model_id,
                "path": str(info.path),
                "architecture": info.architecture,
                "torch_dtype": info.torch_dtype,
                "quantization": info.quantization,
                "size_bytes": info.total_size_bytes,
                "estimated_ram_bytes": info.estimated_ram_bytes,
            }
            for info in self._models.values()
        ]

    def list_model_names(self) -> list[str]:
        """Return just the model IDs."""
        return sorted(self._models)

    def health(self) -> bool:
        """True when at least one local model directory was discovered."""
        return len(self._models) > 0

    def model_exists(self, name: str) -> bool:
        """Check whether a model ID is present locally."""
        return name in self._models

    def pull_model(self, name: str) -> None:
        raise NotImplementedError(
            "LocalModelClient does not support pulling. Add the model files to "
            f"{self.models_dir} and restart the service."
        )

    def delete_model(self, name: str) -> None:
        raise NotImplementedError(
            "LocalModelClient does not support deletion. Remove files manually from "
            f"{self.models_dir}."
        )

    # ------------------------------------------------------------------
    # Inference implementation (lazily imported)
    # ------------------------------------------------------------------
    def _run_transformers_chat(
        self, request: ChatRequest, info: LocalModelInfo
    ) -> ChatResponse:
        """Load model via transformers and generate a response."""
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Local inference requires 'transformers' and 'torch'. "
                "Install them in the virtual environment: "
                "pip install transformers torch"
            ) from exc

        device = "cuda" if torch.cuda.is_available() else "cpu"

        tokenizer = AutoTokenizer.from_pretrained(info.path, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            info.path,
            torch_dtype="auto",
            device_map="auto" if device == "cuda" else None,
            trust_remote_code=True,
        )

        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        inputs = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            return_tensors="pt",
            return_dict=True,
        )
        if device == "cuda":
            inputs = {k: v.to(model.device) for k, v in inputs.items()}

        with torch.inference_mode():
            outputs = model.generate(
                **inputs,
                max_new_tokens=request.max_tokens,
                do_sample=request.mode != InferenceMode.DETERMINISTIC,
                temperature=0.0 if request.mode == InferenceMode.DETERMINISTIC else 0.7,
                top_p=1.0,
                pad_token_id=tokenizer.pad_token_id,
            )

        generated_tokens = outputs[0][inputs["input_ids"].shape[1] :]
        content = tokenizer.decode(generated_tokens, skip_special_tokens=True)
        prompt_tokens = inputs["input_ids"].shape[1]
        completion_tokens = len(generated_tokens)

        return ChatResponse(
            message=ChatMessage(role="assistant", content=content),
            model=request.model,
            created_at_ns=time.time_ns(),
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            finish_reason="stop",
        )
