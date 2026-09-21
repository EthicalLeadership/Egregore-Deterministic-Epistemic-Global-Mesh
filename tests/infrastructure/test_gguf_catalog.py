"""Tests for the GGUF catalog filesystem mirror."""

from __future__ import annotations

import json

import pytest

from egregore.infrastructure import gguf_catalog
from egregore.infrastructure.gguf_catalog import GGUFCatalog, GGUFEntry


@pytest.fixture
def models_root(tmp_path, monkeypatch):
    """Redirect the catalog module at a tmp models root with fake .gguf files."""
    root = tmp_path / "models"
    gguf = root / "gguf"
    for tier, name in [
        ("expert", "Big-7B-Instruct-Q4_K_M.gguf"),
        ("general", "small-1.5b-instruct-q4_k_m.gguf"),
        ("specialized", "coder-6.7b-instruct.Q4_K_M.gguf"),
    ]:
        d = gguf / tier
        d.mkdir(parents=True)
        (d / name).write_bytes(b"fake-gguf")
    # Backup file must never be catalogued.
    (gguf / "specialized" / "coder-6.7b-instruct.Q4_K_M.gguf.bak-pre-template").write_bytes(b"bak")

    monkeypatch.setattr(gguf_catalog, "MODELS_ROOT", root)
    monkeypatch.setattr(gguf_catalog, "GGUF_ROOT", gguf)
    monkeypatch.setattr(gguf_catalog, "EXPERT_DIR", gguf / "expert")
    monkeypatch.setattr(gguf_catalog, "GENERAL_DIR", gguf / "general")
    monkeypatch.setattr(gguf_catalog, "SPECIALIZED_DIR", gguf / "specialized")
    monkeypatch.setattr(gguf_catalog, "CATALOG_FILE", gguf / ".catalog.json")
    return root


def test_scan_mirrors_filesystem_with_raw_keys(models_root):
    catalog = GGUFCatalog()
    keys = sorted(catalog.list_models())
    assert keys == [
        "expert/Big-7B-Instruct-Q4_K_M",
        "general/small-1.5b-instruct-q4_k_m",
        "specialized/coder-6.7b-instruct.Q4_K_M",
    ]


def test_scan_derives_informational_metadata(models_root):
    catalog = GGUFCatalog()
    entry = catalog.get("expert/Big-7B-Instruct-Q4_K_M")
    assert entry is not None
    assert entry.quantization == "Q4_K_M"
    assert entry.parameters == "7B"
    assert entry.size_bytes == len(b"fake-gguf")


def test_corrupt_catalog_triggers_rescan(models_root):
    gguf_catalog.CATALOG_FILE.write_text("{corrupt")
    catalog = GGUFCatalog()
    assert len(catalog.list_models()) == 3
    # Rewritten as valid JSON.
    raw = json.loads(gguf_catalog.CATALOG_FILE.read_text())
    assert len(raw["entries"]) == 3


def test_stale_entry_pruned_on_load(models_root):
    catalog = GGUFCatalog()
    catalog.register(
        GGUFEntry(
            model_id="ghost",
            filename="ghost-Q4_K_M.gguf",
            tier="general",
            quantization="Q4_K_M",
            parameters="1B",
            size_bytes=1,
            sha256="x",
        )
    )
    assert catalog.get("ghost") is not None
    # File never existed on disk -> pruned on next load.
    fresh = GGUFCatalog()
    assert fresh.get("ghost") is None


def test_save_refuses_path_outside_root(models_root, monkeypatch):
    monkeypatch.setattr(
        gguf_catalog, "CATALOG_FILE", models_root.parent / "evil" / ".catalog.json"
    )
    catalog = GGUFCatalog.__new__(GGUFCatalog)
    catalog._entries = {}
    with pytest.raises(ValueError, match="outside models root"):
        catalog._save()


def test_register_and_verify_all_work(models_root):
    """Regression: `import time` was missing, breaking register/verify_all."""
    catalog = GGUFCatalog()
    target = gguf_catalog.GGUF_ROOT / "general" / "small-1.5b-instruct-q4_k_m.gguf"
    entry = GGUFEntry(
        model_id="registered",
        filename=target.name,
        tier="general",
        quantization="Q4_K_M",
        parameters="1B",
        size_bytes=target.stat().st_size,
        sha256="",
    )
    catalog.register(entry)  # must not raise NameError
    results = catalog.verify_all()  # must not raise NameError
    assert results["registered"] == "VERIFIED"


def test_verify_all_detects_corruption(models_root):
    catalog = GGUFCatalog()
    catalog.verify_all()  # caches real hashes
    target = gguf_catalog.GGUF_ROOT / "general" / "small-1.5b-instruct-q4_k_m.gguf"
    target.write_bytes(b"tampered")
    results = GGUFCatalog().verify_all()
    assert results["general/small-1.5b-instruct-q4_k_m"] == "CORRUPT"


def test_legacy_alias_resolves(models_root, monkeypatch):
    monkeypatch.setitem(
        gguf_catalog._LEGACY_ALIASES,
        "legacy-big",
        "expert/Big-7B-Instruct-Q4_K_M",
    )
    catalog = GGUFCatalog()
    entry = catalog.get("legacy-big")
    assert entry is not None
    assert entry.model_id == "expert/Big-7B-Instruct-Q4_K_M"


def test_get_catalog_shape(models_root):
    catalog = GGUFCatalog()
    data = catalog.get_catalog()
    entry = data["expert/Big-7B-Instruct-Q4_K_M"]
    assert set(entry) == {"file_path", "size_bytes", "quantization", "parameters"}
    assert entry["file_path"].endswith("expert/Big-7B-Instruct-Q4_K_M.gguf")
