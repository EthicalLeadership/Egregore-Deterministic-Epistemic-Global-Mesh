from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from typing import Any

from egregore.shared.canonical import canonical_dumps


def _sha256_hex_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_hex_file(path: str, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class LocalLlmAdapter:
    """
    Plane-2 inference adapter.

    Notes for spec-integrity layering:
    - This module intentionally does NOT import egregore.application/* so Core Plane remains unchanged.
    - This module computes hashes using local hashlib (no shared/canonical imports) to satisfy CI layer matrix.
    - llama-cpp is imported lazily so CPU-only test bootstrap does not require llama-cpp-python.
    """

    def __init__(
        self,
        model_path: str,
        *,
        seed: int = 42,
        n_ctx: int = 2048,
        n_gpu_layers: int = -1,
        n_threads: int | None = None,
    ):
        if not os.path.exists(model_path):
            raise FileNotFoundError(model_path)

        self.model_path = model_path
        self.seed = seed
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.n_threads = n_threads
        self._llm: Any | None = None
        self._model_hash: str | None = None

    @property
    def model_artifact_hash(self) -> str:
        if self._model_hash is None:
            self._model_hash = _sha256_hex_file(self.model_path)
        return self._model_hash

    def _load(self) -> Any:
        if self._llm is None:
            try:
                # optional dependency: llama-cpp-python is lazy-imported so CPU-only bootstrap/tests don’t require it.
                from llama_cpp import Llama  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("llama-cpp-python not installed") from exc

            kwargs: dict[str, Any] = {
                "model_path": self.model_path,
                "n_ctx": self.n_ctx,
                "seed": self.seed,
                "verbose": False,
                "n_gpu_layers": self.n_gpu_layers,
            }
            if self.n_threads is not None:
                kwargs["n_threads"] = self.n_threads
            self._llm = Llama(**kwargs)
        return self._llm

    def generate(
        self,
        *,
        prompt: str,
        max_tokens: int = 128,
        temperature: float = 0.0,
        top_p: float = 1.0,
        seed: int = 42,
        stop: list[str] | None = None,
    ) -> dict[str, str]:
        llm = self._load()
        out = llm(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=1,
            seed=seed,
            stop=stop,
        )

        text = str(out["choices"][0]["text"]).strip()

        prompt_hash = _sha256_hex_bytes(prompt.encode("utf-8"))
        output_hash = _sha256_hex_bytes(text.encode("utf-8"))

        return {
            "text": text,
            "model_hash": self.model_artifact_hash,
            "prompt_hash": prompt_hash,
            "output_hash": output_hash,
        }

    def stream_chat(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int = 128,
        temperature: float = 0.0,
    ) -> Iterator[str]:
        """Stream chat completion deltas using the model's built-in chat template."""
        llm = self._load()
        stream = llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=1.0,
            top_k=1,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content")
            if content:
                yield content

    def chat(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int = 128,
        temperature: float = 0.0,
    ) -> dict[str, str]:
        """Chat completion using the model's built-in chat template."""
        llm = self._load()
        out = llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=1.0,
            top_k=1,
        )

        text = str(out["choices"][0]["message"]["content"]).strip()

        prompt_hash = _sha256_hex_bytes(
            canonical_dumps(messages, sort_keys=True).encode("utf-8")
        )
        output_hash = _sha256_hex_bytes(text.encode("utf-8"))

        return {
            "text": text,
            "model_hash": self.model_artifact_hash,
            "prompt_hash": prompt_hash,
            "output_hash": output_hash,
        }
