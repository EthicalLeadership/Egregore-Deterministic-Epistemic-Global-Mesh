"""Tests for the egregore-module.json schema validator."""

from __future__ import annotations

from typing import Any

from egregore.pipeline.manifest_validator import validate_manifest


def _valid_manifest(**overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "name": "demo",
        "version": "0.1.0",
        "plane": "plane1",
        "layer": "kernel",
        "author": "test",
        "signature": "ed25519:00",
        "source": {"repository": "https://example.com/repo.git", "commit": "a" * 40},
        "build": {"system": "custom"},
    }
    manifest.update(overrides)
    return manifest


def test_valid_manifest_passes() -> None:
    assert validate_manifest(_valid_manifest()) == []


def test_missing_required_fields() -> None:
    errors = validate_manifest({})
    fields = {e["field"] for e in errors}
    assert {"name", "version", "plane", "layer", "author", "signature"} <= fields


def test_invalid_plane() -> None:
    errors = validate_manifest(_valid_manifest(plane="plane3"))
    assert any(
        e["field"] == "plane" and "Invalid value" in e["message"] for e in errors
    )


def test_invalid_layer() -> None:
    errors = validate_manifest(_valid_manifest(layer="unknown"))
    assert any("Unknown layer" in e["message"] for e in errors)


def test_signature_must_be_ed25519() -> None:
    errors = validate_manifest(_valid_manifest(signature="rsa:00"))
    assert any(e["field"] == "signature" for e in errors)


def test_source_shape_enforced() -> None:
    errors = validate_manifest(_valid_manifest(source={"repository": "x"}))
    assert any(e["field"] == "source.commit" for e in errors)


def test_build_dependency_hash_must_be_sha256() -> None:
    errors = validate_manifest(
        _valid_manifest(
            build={
                "system": "custom",
                "dependencies": [
                    {"name": "dep", "version": "1.0.0", "hash": "md5:bad"}
                ],
            }
        )
    )
    assert any(e["field"] == "build.dependencies[0].hash" for e in errors)


def test_capabilities_must_be_lists_of_strings() -> None:
    errors = validate_manifest(_valid_manifest(capabilities={"read": [123]}))
    assert any(e["field"] == "capabilities.read[0]" for e in errors)


def test_ports_must_be_lists_of_strings() -> None:
    errors = validate_manifest(_valid_manifest(ports={"implements": [42]}))
    assert any(e["field"] == "ports.implements[0]" for e in errors)


def test_cbi0_must_be_object() -> None:
    errors = validate_manifest(_valid_manifest(cbi0="not-a-dict"))
    assert any(e["field"] == "cbi0" for e in errors)
