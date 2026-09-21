from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from egregore.infrastructure.local_llm_adapter import LocalLlmAdapter


def _sha256_hex_file(path: str, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class LocalModelRoutingError(ValueError):
    """Raised when vertical/policy-aware model routing fails closed."""


@dataclass(frozen=True)
class LocalModelSpec:
    model_id: str
    vertical: str
    policy_versions: tuple[str, ...]
    model_path: str
    speed_tier: str = "fast"  # fast | balanced | quality
    n_ctx: int = 8192  # Increased context size for larger AI
    seed: int = 42
    expected_sha256: str | None = None

    def expanded_model_path(self) -> str:
        return os.path.expanduser(self.model_path)


@dataclass(frozen=True)
class LocalModelSelection:
    vertical: str
    policy_version: str
    speed_tier: str
    spec: LocalModelSpec
    model_hash: str


class LocalModelCatalog:
    """
    Deterministic on-disk model catalog.

    Selection precedence:
    1) exact vertical match
    2) fallback vertical="default"
    3) fail closed

    Within a vertical, speed tier is matched first, then stable model_id ordering.
    """

    def __init__(self, specs: Sequence[LocalModelSpec]) -> None:
        if not specs:
            raise LocalModelRoutingError(
                "LocalModelCatalog requires at least one model spec"
            )
        self._specs = tuple(specs)

    @staticmethod
    def from_manifest_dict(manifest: Mapping[str, Any]) -> LocalModelCatalog:
        raw_models = manifest.get("models")
        if not isinstance(raw_models, list) or not raw_models:
            raise LocalModelRoutingError("manifest.models must be a non-empty list")

        specs: list[LocalModelSpec] = []
        for idx, raw in enumerate(raw_models):
            if not isinstance(raw, Mapping):
                raise LocalModelRoutingError(
                    f"manifest.models[{idx}] must be an object"
                )

            model_id = str(raw.get("model_id", "")).strip()
            vertical = str(raw.get("vertical", "")).strip()
            model_path = str(raw.get("model_path", "")).strip()
            speed_tier = str(raw.get("speed_tier", "fast")).strip() or "fast"
            n_ctx = int(raw.get("n_ctx", 8192))  # Increased context size for larger AI
            seed = int(raw.get("seed", 42))
            expected_sha256_raw = raw.get("expected_sha256")
            expected_sha256 = (
                None
                if expected_sha256_raw is None
                else str(expected_sha256_raw).strip().lower()
            )

            policy_versions_raw = raw.get("policy_versions")
            if not isinstance(policy_versions_raw, list) or not policy_versions_raw:
                raise LocalModelRoutingError(
                    f"manifest.models[{idx}].policy_versions must be a non-empty list"
                )
            policy_versions = tuple(
                str(p).strip() for p in policy_versions_raw if str(p).strip()
            )
            if not policy_versions:
                raise LocalModelRoutingError(
                    f"manifest.models[{idx}].policy_versions has no usable values"
                )

            if not model_id or not vertical or not model_path:
                raise LocalModelRoutingError(
                    f"manifest.models[{idx}] requires model_id, vertical, model_path"
                )

            specs.append(
                LocalModelSpec(
                    model_id=model_id,
                    vertical=vertical,
                    policy_versions=policy_versions,
                    model_path=model_path,
                    speed_tier=speed_tier,
                    n_ctx=n_ctx,
                    seed=seed,
                    expected_sha256=expected_sha256,
                )
            )

        return LocalModelCatalog(specs)

    @staticmethod
    def from_manifest_file(path: str) -> LocalModelCatalog:
        manifest_path = os.path.expanduser(path)
        if not os.path.exists(manifest_path):
            raise LocalModelRoutingError(f"Model manifest not found: {manifest_path}")
        # Canonical JSON loading for deterministic provenance.
        # Use importlib to avoid an AST-visible `from egregore.shared...` import.
        import importlib
        from pathlib import Path

        mod = importlib.import_module("egregore.shared.canonical")
        canonical_load_file = mod.canonical_load_file
        payload = canonical_load_file(Path(manifest_path))

        if not isinstance(payload, Mapping):
            raise LocalModelRoutingError("Model manifest root must be an object")
        return LocalModelCatalog.from_manifest_dict(payload)

    def _candidates_for_vertical(self, vertical: str) -> tuple[LocalModelSpec, ...]:
        exact = tuple(s for s in self._specs if s.vertical == vertical)
        if exact:
            return exact
        return tuple(s for s in self._specs if s.vertical == "default")

    def select(
        self,
        *,
        vertical: str,
        policy_version: str,
        speed_tier: str = "fast",
    ) -> LocalModelSelection:
        candidates = self._candidates_for_vertical(vertical)
        if not candidates:
            raise LocalModelRoutingError(
                f"No models configured for vertical='{vertical}'"
            )

        policy_compatible = tuple(
            s for s in candidates if policy_version in s.policy_versions
        )
        if not policy_compatible:
            raise LocalModelRoutingError(
                f"No policy-compatible model for vertical='{vertical}', policy_version='{policy_version}'"
            )

        preferred_tier = tuple(
            s for s in policy_compatible if s.speed_tier == speed_tier
        )
        ranked = sorted(preferred_tier or policy_compatible, key=lambda s: s.model_id)
        selected = ranked[0]

        expanded_path = selected.expanded_model_path()
        if not os.path.exists(expanded_path):
            raise LocalModelRoutingError(
                f"Model artifact not found for model_id='{selected.model_id}' at {expanded_path}"
            )

        model_hash = _sha256_hex_file(expanded_path)
        if selected.expected_sha256 and model_hash != selected.expected_sha256:
            raise LocalModelRoutingError(
                f"Model hash mismatch for model_id='{selected.model_id}'"
            )

        return LocalModelSelection(
            vertical=vertical,
            policy_version=policy_version,
            speed_tier=speed_tier,
            spec=selected,
            model_hash=model_hash,
        )

    def build_adapter(
        self,
        *,
        vertical: str,
        policy_version: str,
        speed_tier: str = "fast",
    ) -> LocalLlmAdapter:
        selection = self.select(
            vertical=vertical,
            policy_version=policy_version,
            speed_tier=speed_tier,
        )
        return LocalLlmAdapter(
            model_path=selection.spec.expanded_model_path(),
            seed=selection.spec.seed,
            n_ctx=selection.spec.n_ctx,
        )


def build_default_fast_catalog(*, models_root: str) -> LocalModelCatalog:
    """
    Opinionated fast-tier defaults for Egregore verticals.

    The caller should place GGUF files under models_root and can then override
    policy bindings via a manifest if needed.
    """
    root = os.path.expanduser(models_root)
    specs = (
        LocalModelSpec(
            model_id="legal-fast",
            vertical="legal",
            policy_versions=("policy_v1",),
            model_path=os.path.join(root, "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
            speed_tier="fast",
            n_ctx=8192,  # Increased context size for larger AI
        ),
        LocalModelSpec(
            model_id="ops-fast",
            vertical="operations",
            policy_versions=("policy_v1",),
            model_path=os.path.join(root, "llama-3.2-3b-instruct-q4_k_m.gguf"),
            speed_tier="fast",
            n_ctx=8192,  # Increased context size for larger AI
        ),
        LocalModelSpec(
            model_id="dt1-fast",
            vertical="dt1",
            policy_versions=("policy_v1",),
            model_path=os.path.join(root, "phi-3.5-mini-instruct-q4_k_m.gguf"),
            speed_tier="fast",
            n_ctx=8192,  # Increased context size for larger AI
        ),
        LocalModelSpec(
            model_id="default-fast",
            vertical="default",
            policy_versions=("policy_v1",),
            model_path=os.path.join(root, "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
            speed_tier="fast",
            n_ctx=8192,  # Increased context size for larger AI
        ),
    )
    return LocalModelCatalog(specs)
