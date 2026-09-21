"""Tests for the deterministic ModelSelector (catalog mirror + manifest)."""

from __future__ import annotations

import json

import pytest

from egregore.application.model_selector import (
    ModelSelector,
    NoModelAvailableError,
    load_profiles,
)


def _catalog(*keys: str, size: int = 1000) -> dict:
    return {
        k: {
            "file_path": f"/models/gguf/{k}.gguf",
            "size_bytes": size,
            "quantization": "Q4_K_M",
            "parameters": "7B",
        }
        for k in keys
    }


def _profiles() -> dict:
    return {
        "model-a": {
            "catalog_key": "specialized/a-Q4_K_M",
            "routes_to": "a",
            "task_tags": ["code"],
            "quality_score": 80,
        },
        "model-b": {
            "catalog_key": "expert/b-Q4_K_M",
            "routes_to": "b",
            "task_tags": ["general", "legal"],
            "quality_score": 85,
        },
        "model-c": {
            "catalog_key": "general/c-Q4_K_M",
            "routes_to": "c",
            "task_tags": ["fast", "general"],
            "quality_score": 55,
        },
    }


FULL_CATALOG = _catalog(
    "specialized/a-Q4_K_M", "expert/b-Q4_K_M", "general/c-Q4_K_M"
)


def test_select_picks_highest_quality_for_task():
    s = ModelSelector(FULL_CATALOG, _profiles())
    assert s.select("legal").routes_to == "b"
    assert s.select("code").routes_to == "a"
    assert s.select("fast").routes_to == "c"


def test_select_general_prefers_higher_quality():
    s = ModelSelector(FULL_CATALOG, _profiles())
    # b and c both tagged general; b has higher quality.
    assert s.select("general").routes_to == "b"


def test_select_skips_profile_whose_file_is_missing():
    catalog = _catalog("expert/b-Q4_K_M", "general/c-Q4_K_M")
    s = ModelSelector(catalog, _profiles())
    result = s.select("code")  # model-a's file is gone
    assert result.fallback_used is True
    assert result.routes_to == "b"  # best remaining


def test_tie_break_equal_quality_prefers_smaller_size():
    catalog = _catalog("specialized/a-Q4_K_M", size=5000)
    catalog["specialized/d-Q4_K_M"] = {
        "file_path": "/models/gguf/specialized/d-Q4_K_M.gguf",
        "size_bytes": 1000,
        "quantization": "Q4_K_M",
        "parameters": "7B",
    }
    profiles = {
        "model-a": {
            "catalog_key": "specialized/a-Q4_K_M",
            "routes_to": "a",
            "task_tags": ["code"],
            "quality_score": 90,
        },
        "model-d": {
            "catalog_key": "specialized/d-Q4_K_M",
            "routes_to": "d",
            "task_tags": ["code"],
            "quality_score": 90,
        },
    }
    s = ModelSelector(catalog, profiles)
    assert s.select("code").routes_to == "d"  # smaller size wins the tie


def test_tie_break_equal_quality_and_size_uses_logical_id():
    catalog = _catalog("specialized/a-Q4_K_M", "specialized/d-Q4_K_M", size=1000)
    profiles = {
        "model-z": {
            "catalog_key": "specialized/d-Q4_K_M",
            "routes_to": "z",
            "task_tags": ["code"],
            "quality_score": 90,
        },
        "model-y": {
            "catalog_key": "specialized/a-Q4_K_M",
            "routes_to": "y",
            "task_tags": ["code"],
            "quality_score": 90,
        },
    }
    s = ModelSelector(catalog, profiles)
    assert s.select("code").logical_id == "model-y"  # alphabetical


def test_empty_catalog_raises():
    s = ModelSelector({}, _profiles())
    with pytest.raises(NoModelAvailableError):
        s.select("general")


def test_invalid_task_raises():
    s = ModelSelector(FULL_CATALOG, _profiles())
    with pytest.raises(ValueError, match="Invalid task"):
        s.select("bogus")


def test_load_profiles_valid(tmp_path):
    p = tmp_path / "profiles.json"
    p.write_text(json.dumps(_profiles()))
    loaded = load_profiles(str(p))
    assert loaded["model-a"]["routes_to"] == "a"


def test_load_profiles_corrupt_json_fails_closed(tmp_path):
    p = tmp_path / "profiles.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="unreadable"):
        load_profiles(str(p))


def test_load_profiles_missing_file_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="unreadable"):
        load_profiles(str(tmp_path / "nope.json"))


def test_load_profiles_missing_field_fails_closed(tmp_path):
    p = tmp_path / "profiles.json"
    bad = _profiles()
    del bad["model-a"]["quality_score"]
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="missing fields"):
        load_profiles(str(p))
