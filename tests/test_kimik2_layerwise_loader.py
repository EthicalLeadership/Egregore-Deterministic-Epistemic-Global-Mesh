"""Tests for the host-backed Kimi K2 loader selection (IKimik2Loader wiring).

Uses dummy artifacts + ``KIMIK2_TEST_MODE=1`` so no real weights are loaded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("transformers")


def _dummy_model_dir(tmp_path: Path) -> str:
    d = tmp_path / "kimi-k2-base"
    d.mkdir()
    (d / "model.safetensors.index.json").write_text("{}")
    for i in range(61):
        (d / f"model-{i + 1}-of-61.safetensors").write_text("")
    (d / "config.json").write_text("{}")
    (d / "tokenizer_config.json").write_text("{}")
    return str(d)


def test_build_kimik2_loader_defaults_to_layerwise(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMIK2_TEST_MODE", "1")
    monkeypatch.delenv("EGREGORE_KIMIK2_USE_LAYERWISE", raising=False)

    from egregore.infrastructure.kimik2_layerwise_loader_adapter import (
        Kimik2LayerWiseLoaderAdapter,
        build_kimik2_loader,
    )

    loader = build_kimik2_loader(_dummy_model_dir(tmp_path))
    assert isinstance(loader, Kimik2LayerWiseLoaderAdapter)


def test_build_kimik2_loader_falls_back_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMIK2_TEST_MODE", "1")
    monkeypatch.setenv("EGREGORE_KIMIK2_USE_LAYERWISE", "0")

    from egregore.infrastructure.kimik2_loader_adapter import Kimik2LoaderAdapter
    from egregore.infrastructure.kimik2_layerwise_loader_adapter import (
        build_kimik2_loader,
    )

    loader = build_kimik2_loader(_dummy_model_dir(tmp_path))
    assert isinstance(loader, Kimik2LoaderAdapter)


def test_layerwise_generate_rejects_nonzero_temperature(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMIK2_TEST_MODE", "1")
    from egregore.infrastructure.kimik2_layerwise_loader_adapter import (
        Kimik2LayerWiseLoaderAdapter,
    )
    from egregore.interface.semantics_ports import Kimik2LoaderError

    loader = Kimik2LayerWiseLoaderAdapter(_dummy_model_dir(tmp_path))
    with pytest.raises(Kimik2LoaderError):
        loader.generate("hello", max_tokens=4, temperature=0.5)


def test_layerwise_generate_errors_when_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMIK2_TEST_MODE", "1")
    from egregore.infrastructure.kimik2_layerwise_loader_adapter import (
        Kimik2LayerWiseLoaderAdapter,
    )
    from egregore.interface.semantics_ports import Kimik2LoaderError

    loader = Kimik2LayerWiseLoaderAdapter(_dummy_model_dir(tmp_path))
    with pytest.raises(Kimik2LoaderError):
        loader.generate("hello", max_tokens=4)


def test_layerwise_validate_artifacts_missing_shard(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMIK2_TEST_MODE", "1")
    from egregore.infrastructure.kimik2_layerwise_loader_adapter import (
        Kimik2LayerWiseLoaderAdapter,
    )
    from egregore.interface.semantics_ports import Kimik2LoaderError

    d = tmp_path / "kimi-k2-base"
    d.mkdir()
    (d / "model.safetensors.index.json").write_text("{}")
    for i in range(60):  # one shard short
        (d / f"model-{i + 1}-of-61.safetensors").write_text("")
    (d / "config.json").write_text("{}")
    (d / "tokenizer_config.json").write_text("{}")

    with pytest.raises(Kimik2LoaderError):
        Kimik2LayerWiseLoaderAdapter(str(d))
