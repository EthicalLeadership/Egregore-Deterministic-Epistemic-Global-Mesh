# epistemic marker: provenance / auditability
"""Lazy-loading GGUF model host for cell execution.

This module mirrors the model-host pattern used by
``egregore.interface.factory_router`` but lives in the ``cells`` layer so that
cell execution remains independent of the HTTP interface layer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# PyYAML has no PEP 561 stubs; ignore for compatibility.
import yaml  # type: ignore[import-untyped]

logger = logging.getLogger("egregore.cells.model_host")


try:
    from llama_cpp import Llama

    _LLAMA_AVAILABLE = True
except Exception as exc:  # noqa: BLE001
    _LLAMA_AVAILABLE = False
    _LLAMA_IMPORT_ERROR = str(exc)


def _default_profile_paths() -> list[Path]:
    return [
        Path(__file__).resolve().parents[3] / "config" / "factory_profiles_v2.yaml",
        Path("/opt/egregore/config/factory_profiles_v2.yaml"),
        Path("config/factory_profiles_v2.yaml"),
    ]


def load_factory_profiles(
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Load factory model profiles from the standard locations."""
    paths = [Path(path)] if path is not None else _default_profile_paths()

    for candidate in paths:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

    raise FileNotFoundError(
        "factory_profiles_v2.yaml not found; searched: "
        + ", ".join(str(p) for p in paths)
    )


def resolve_model_specs(
    cell_models: list[dict[str, Any]],
    profiles: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Merge cell-specific model references with global factory profiles.

    Each ``cell_models`` entry may use ``path_or_alias`` (or the legacy ``path``
    alias) to either name a model declared in ``profiles["models"]`` or point to
    a concrete GGUF file. The returned dict maps ``model_id`` to a spec dict
    suitable for ``ModelHost.get``.
    """
    if profiles is None:
        profiles = load_factory_profiles()

    global_models = profiles.get("models", {})
    resolved: dict[str, dict[str, Any]] = {}

    for ref in cell_models:
        model_id = ref.get("model_id")
        if not model_id:
            continue
        path_or_alias = ref.get("path_or_alias") or ref.get("path")

        spec: dict[str, Any]
        if path_or_alias in global_models:
            spec = dict(global_models[path_or_alias])
        else:
            spec = {"path": path_or_alias}

        # Cell-level overrides win.
        for key in ("n_ctx", "n_gpu_layers", "chat_format", "family"):
            if key in ref:
                spec[key] = ref[key]

        resolved[model_id] = spec

    return resolved


@dataclass
class ModelHost:
    """Loads Llama models on first use and caches them for the process lifetime."""

    model_specs: dict[str, dict[str, Any]]
    _cache: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def get(self, model_id: str) -> Any:
        if model_id in self._cache:
            return self._cache[model_id]

        spec = self.model_specs.get(model_id)
        if spec is None:
            raise KeyError(f"Unknown model '{model_id}'")

        path = Path(spec.get("path", ""))
        if not path.exists():
            raise FileNotFoundError(
                f"Model '{model_id}' not found at {path}. "
                "Run scripts/download_factory_models.sh or update factory_profiles_v2.yaml."
            )

        if not _LLAMA_AVAILABLE:
            raise RuntimeError(
                f"llama-cpp-python is not available: {_LLAMA_IMPORT_ERROR}"
            )

        logger.info("Loading model %s from %s", model_id, path)
        start = time.monotonic()
        llm = Llama(
            model_path=str(path),
            n_ctx=spec.get("n_ctx", 8192),
            n_gpu_layers=spec.get("n_gpu_layers", -1),
            chat_format=spec.get("chat_format", None),
            verbose=False,
        )
        elapsed = (time.monotonic() - start) * 1000
        logger.info("Model %s loaded in %.1f ms", model_id, elapsed)
        self._cache[model_id] = llm
        return llm

    def health(self) -> dict[str, Any]:
        return {
            "llama_cpp_available": _LLAMA_AVAILABLE,
            "cached_models": list(self._cache.keys()),
            "configured_models": {
                mid: {
                    "path": spec.get("path"),
                    "exists": Path(spec.get("path", "")).exists(),
                }
                for mid, spec in self.model_specs.items()
            },
        }
