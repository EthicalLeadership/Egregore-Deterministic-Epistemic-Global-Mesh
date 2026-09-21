from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from egregore.infrastructure.local_model_catalog import (
    LocalModelCatalog,
    LocalModelRoutingError,
    build_default_fast_catalog,
)


def _write_model(path: Path, payload: bytes) -> str:
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_select_vertical_policy_and_speed_tier_prefers_matching_spec(
    tmp_path: Path,
) -> None:
    legal_fast = tmp_path / "legal-fast.gguf"
    legal_quality = tmp_path / "legal-quality.gguf"
    hash_fast = _write_model(legal_fast, b"legal-fast-model")
    _write_model(legal_quality, b"legal-quality-model")

    catalog = LocalModelCatalog.from_manifest_dict(
        {
            "models": [
                {
                    "model_id": "legal-quality",
                    "vertical": "legal",
                    "policy_versions": ["policy_v1"],
                    "model_path": str(legal_quality),
                    "speed_tier": "quality",
                },
                {
                    "model_id": "legal-fast",
                    "vertical": "legal",
                    "policy_versions": ["policy_v1"],
                    "model_path": str(legal_fast),
                    "speed_tier": "fast",
                    "expected_sha256": hash_fast,
                },
            ]
        }
    )

    selected = catalog.select(
        vertical="legal", policy_version="policy_v1", speed_tier="fast"
    )
    assert selected.spec.model_id == "legal-fast"
    assert selected.model_hash == hash_fast


def test_select_fails_closed_on_policy_mismatch(tmp_path: Path) -> None:
    model_path = tmp_path / "ops.gguf"
    _write_model(model_path, b"ops-model")

    catalog = LocalModelCatalog.from_manifest_dict(
        {
            "models": [
                {
                    "model_id": "ops-fast",
                    "vertical": "operations",
                    "policy_versions": ["policy_v1"],
                    "model_path": str(model_path),
                }
            ]
        }
    )

    with pytest.raises(LocalModelRoutingError, match="No policy-compatible model"):
        catalog.select(vertical="operations", policy_version="policy_v2")


def test_select_fails_closed_on_hash_mismatch(tmp_path: Path) -> None:
    model_path = tmp_path / "dt1.gguf"
    _write_model(model_path, b"dt1-model")

    catalog = LocalModelCatalog.from_manifest_dict(
        {
            "models": [
                {
                    "model_id": "dt1-fast",
                    "vertical": "dt1",
                    "policy_versions": ["policy_v1"],
                    "model_path": str(model_path),
                    "expected_sha256": "00" * 32,
                }
            ]
        }
    )

    with pytest.raises(LocalModelRoutingError, match="Model hash mismatch"):
        catalog.select(vertical="dt1", policy_version="policy_v1")


def test_manifest_file_load_and_default_vertical_fallback(tmp_path: Path) -> None:
    default_path = tmp_path / "default.gguf"
    _write_model(default_path, b"default-model")

    manifest = {
        "models": [
            {
                "model_id": "default-fast",
                "vertical": "default",
                "policy_versions": ["policy_v1"],
                "model_path": str(default_path),
            }
        ]
    }
    manifest_path = tmp_path / "catalog.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    catalog = LocalModelCatalog.from_manifest_file(str(manifest_path))
    selected = catalog.select(vertical="unknown-vertical", policy_version="policy_v1")
    assert selected.spec.model_id == "default-fast"


def test_build_default_fast_catalog_uses_known_vertical_ids(tmp_path: Path) -> None:
    root = tmp_path
    _write_model(root / "qwen2.5-1.5b-instruct-q4_k_m.gguf", b"qwen")
    _write_model(root / "llama-3.2-3b-instruct-q4_k_m.gguf", b"llama")
    _write_model(root / "phi-3.5-mini-instruct-q4_k_m.gguf", b"phi")

    catalog = build_default_fast_catalog(models_root=str(root))
    legal = catalog.select(vertical="legal", policy_version="policy_v1")
    dt1 = catalog.select(vertical="dt1", policy_version="policy_v1")

    assert legal.spec.model_id == "legal-fast"
    assert dt1.spec.model_id == "dt1-fast"
