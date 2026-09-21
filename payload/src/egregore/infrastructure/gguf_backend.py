"""GGUF backend — llama.cpp in-process inference for the residency layout.

Phase 6 (VRAM residency). Serves quantized GGUF models via llama-cpp-python
with full GPU offload. Two hot residents by design:

- ``my-coder-ft``  -> 7B Q4_K_M (standard final pass)
- ``qwen-1.5b``    -> 1.5B Q4_K_M (QC critic)

Models are lazy-loaded on first use and can be unloaded explicitly for the
heavy-pass swap protocol (see ``egregore.factory.residency``). No Ollama, no
external servers — same in-process rule as CoderBackend.

Env:
    EGREGORE_GGUF_MODELS   name=path,name2=path2 (defaults to the known fleet)
    EGREGORE_GGUF_CTX      context size per model (default 4096)
    EGREGORE_GGUF_ENABLED  set false/off to disable entirely
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import time
from typing import Any

from egregore.domain.inference_models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    InferenceMode,
)
from egregore.interface.llm_ports import ILlmClient

logger = logging.getLogger(__name__)

_DEFAULT_MODELS = {
    "my-coder-ft": "/mnt/blackstar/vol-hdd-a/models/gguf/specialized/my_coder_ft-q4_k_m.gguf",
    "qwen-1.5b": "/mnt/blackstar/vol-hdd-a/models/gguf/general/qwen2.5-1.5b-instruct-q4_k_m.gguf",
}


def _is_enabled() -> bool:
    return os.environ.get("EGREGORE_GGUF_ENABLED", "true").lower() not in (
        "0", "false", "no", "off",
    )


def _parse_models_env() -> dict[str, str]:
    raw = os.environ.get("EGREGORE_GGUF_MODELS", "")
    if not raw.strip():
        return dict(_DEFAULT_MODELS)
    models: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            name, path = pair.split("=", 1)
            models[name.strip()] = path.strip()
    return models or dict(_DEFAULT_MODELS)


class GgufBackend(ILlmClient):
    """Serves one or more GGUF models through llama.cpp with GPU offload.

    Thread-safe via a single load/generate lock (llama.cpp contexts are not
    concurrently safe; the factory is sequential anyway).
    """

    def __init__(self, models: dict[str, str] | None = None, n_ctx: int | None = None):
        self._enabled = _is_enabled()
        self._models = models if models is not None else _parse_models_env()
        self._n_ctx = n_ctx or int(os.environ.get("EGREGORE_GGUF_CTX", "4096"))
        self._instances: dict[str, Any] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ loading
    def _load(self, model: str) -> Any:
        """Lazy-load a GGUF model fully onto the GPU."""
        from llama_cpp import Llama

        path = self._models[model]
        logger.info("GgufBackend loading %s from %s (n_ctx=%d)", model, path, self._n_ctx)
        start = time.monotonic()
        llm = Llama(
            model_path=path,
            n_ctx=self._n_ctx,
            n_gpu_layers=-1,  # full offload
            verbose=False,
        )
        logger.info("GgufBackend loaded %s in %.1fs", model, time.monotonic() - start)
        return llm

    def _get(self, model: str) -> Any:
        if model not in self._models:
            raise RuntimeError(
                f"GgufBackend has no model '{model}'. Known: {sorted(self._models)}"
            )
        with self._lock:
            if model not in self._instances:
                self._instances[model] = self._load(model)
            return self._instances[model]

    def unload(self, model: str) -> None:
        """Release one model's VRAM (swap protocol)."""
        with self._lock:
            llm = self._instances.pop(model, None)
        if llm is not None:
            close = getattr(llm, "close", None)
            if callable(close):
                close()
            del llm
            gc.collect()
            logger.info("GgufBackend unloaded %s", model)

    def unload_all(self) -> None:
        for name in list(self._instances):
            self.unload(name)

    def loaded_models(self) -> list[str]:
        return sorted(self._instances)

    # ------------------------------------------------------------ inference
    def chat(self, request: ChatRequest) -> ChatResponse:
        if not self.health():
            raise RuntimeError("GgufBackend is disabled or has no models configured")
        # Routing prefix (gguf-my-coder-ft) selects this backend; strip for lookup.
        lookup = request.model[5:] if request.model.startswith("gguf-") else request.model
        llm = self._get(lookup)

        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        temperature = 0.0 if request.mode == InferenceMode.DETERMINISTIC else 0.7

        grammar = None
        if request.grammar:
            from llama_cpp import LlamaGrammar

            grammar = LlamaGrammar.from_string(request.grammar, verbose=False)

        with self._lock:
            result = llm.create_chat_completion(
                messages=messages,
                max_tokens=request.max_tokens,
                temperature=temperature,
                # Fixed seed is the foundation of replay determinism (Phase 7).
                seed=request.seed,
                grammar=grammar,
            )

        choice = result["choices"][0]
        usage = result.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        return ChatResponse(
            message=ChatMessage(role="assistant", content=choice["message"].get("content") or ""),
            model=request.model,
            created_at_ns=time.time_ns(),
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            finish_reason=str(choice.get("finish_reason", "stop")),
        )

    def generate(self, prompt: str, model: str | None = None) -> str:
        name = model or next(iter(self._models))
        llm = self._get(name)
        with self._lock:
            result = llm(prompt, max_tokens=2048, temperature=0.0)
        return str(result["choices"][0]["text"])

    # ------------------------------------------------------------ registry
    def list_models(self) -> list[dict[str, Any]]:
        return [
            {"id": name, "path": path, "backend": "gguf", "loaded": name in self._instances}
            for name, path in self._models.items()
        ]

    def health(self) -> bool:
        return self._enabled and bool(self._models)

    def model_exists(self, name: str) -> bool:
        return name in self._models

    def pull_model(self, name: str) -> None:
        raise NotImplementedError("GgufBackend serves local files only")

    def delete_model(self, name: str) -> None:
        raise NotImplementedError("GgufBackend serves local files only")
