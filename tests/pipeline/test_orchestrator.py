"""Tests for the integration-pipeline orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from egregore.pipeline.orchestrator import IntegrationPipeline
from egregore.pipeline.provenance_signer import (
    generate_signing_key,
    load_private_key,
    load_public_key,
    verify_provenance,
)


def _valid_manifest(**overrides: Any) -> dict[str, Any]:
    """Return a manifest that passes ``validate_manifest``."""
    manifest: dict[str, Any] = {
        "name": "demo_module",
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


def _pipeline(tmp_path: Path, **overrides: Any) -> IntegrationPipeline:
    """Build a default pipeline, optionally with a signing key."""
    key_dir = tmp_path / "keys"
    generate_signing_key(key_dir)
    private_key = load_private_key(key_dir / "signing_key.pem")
    return IntegrationPipeline(
        plane1_ports=[],
        concrete_infrastructure=[],
        port_registry=[],
        private_key=private_key,
        **overrides,
    )


def test_run_passes_valid_module(tmp_path: Path) -> None:
    module_dir = tmp_path / "demo_module"
    module_dir.mkdir()
    (module_dir / "egregore-module.json").write_text(
        json.dumps(_valid_manifest()), encoding="utf-8"
    )
    (module_dir / "__init__.py").write_text("x = 1\n", encoding="utf-8")

    pipeline = _pipeline(tmp_path)
    report = pipeline.run(module_dir)

    assert report.module_id == "demo_module"
    assert report.manifest_valid is True
    assert report.is_pass() is True
    assert not report.manifest_errors
    assert not report.load_errors
    assert not any(
        v.get("severity", "error") == "error"
        for v in report.m1_violations + report.m2_violations
    )
    assert report.provenance is not None

    public_key = load_public_key(tmp_path / "keys" / "signing_key.pub")
    assert verify_provenance(report.provenance, public_key) is True

    report_dict = report.to_dict()
    assert report_dict["pass"] is True
    assert report_dict["module_id"] == "demo_module"


def test_run_without_signing_key_does_not_crash(tmp_path: Path) -> None:
    module_dir = tmp_path / "demo_module"
    module_dir.mkdir()
    (module_dir / "egregore-module.json").write_text(
        json.dumps(_valid_manifest()), encoding="utf-8"
    )
    (module_dir / "widget.py").write_text("class Widget:\n    pass\n", encoding="utf-8")

    pipeline = IntegrationPipeline(
        plane1_ports=[], concrete_infrastructure=[], port_registry=[]
    )
    report = pipeline.run(module_dir)

    assert report.is_pass() is True
    assert report.provenance is None


def test_run_missing_manifest(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty_module"
    empty_dir.mkdir()

    pipeline = _pipeline(tmp_path)
    report = pipeline.run(empty_dir)

    assert report.is_pass() is False
    assert report.module_id == "empty_module"
    assert report.manifest_valid is False
    assert any(
        "egregore-module.json not found" in e["message"] for e in report.manifest_errors
    )
    assert not report.load_errors
    assert report.provenance is None


def test_run_invalid_json(tmp_path: Path) -> None:
    module_dir = tmp_path / "bad_json"
    module_dir.mkdir()
    (module_dir / "egregore-module.json").write_text("{not json", encoding="utf-8")

    pipeline = _pipeline(tmp_path)
    report = pipeline.run(module_dir)

    assert report.is_pass() is False
    assert report.manifest_valid is False
    assert any("Invalid JSON" in e["message"] for e in report.manifest_errors)


def test_run_manifest_not_an_object(tmp_path: Path) -> None:
    module_dir = tmp_path / "bad_root"
    module_dir.mkdir()
    (module_dir / "egregore-module.json").write_text("[1, 2, 3]", encoding="utf-8")

    pipeline = _pipeline(tmp_path)
    report = pipeline.run(module_dir)

    assert report.is_pass() is False
    assert report.manifest_valid is False
    assert any("JSON object" in e["message"] for e in report.manifest_errors)


def test_run_manifest_validation_errors(tmp_path: Path) -> None:
    module_dir = tmp_path / "incomplete"
    module_dir.mkdir()
    (module_dir / "egregore-module.json").write_text(
        json.dumps({"name": "incomplete"}), encoding="utf-8"
    )

    pipeline = _pipeline(tmp_path)
    report = pipeline.run(module_dir)

    assert report.is_pass() is False
    assert report.manifest_valid is False
    assert any(e["field"] == "version" for e in report.manifest_errors)
    assert any(e["field"] == "plane" for e in report.manifest_errors)


def test_run_python_syntax_error(tmp_path: Path) -> None:
    module_dir = tmp_path / "syntax_error"
    module_dir.mkdir()
    (module_dir / "egregore-module.json").write_text(
        json.dumps(_valid_manifest()), encoding="utf-8"
    )
    (module_dir / "broken.py").write_text("def foo(\n", encoding="utf-8")
    # A test file should be skipped by the orchestrator.
    (module_dir / "test_broken.py").write_text("def foo(\n", encoding="utf-8")

    pipeline = _pipeline(tmp_path)
    report = pipeline.run(module_dir)

    assert report.manifest_valid is True
    assert report.is_pass() is False
    assert any(
        "syntax error" in e["message"].lower() and e["field"] == "broken.py"
        for e in report.load_errors
    )
    assert not any(e["field"] == "test_broken.py" for e in report.load_errors)
    assert report.m1_violations == []
    assert report.m2_violations == []
    assert report.provenance is None


def test_run_m1_violation_detected(tmp_path: Path) -> None:
    module_dir = tmp_path / "plane1_bad"
    module_dir.mkdir()
    (module_dir / "egregore-module.json").write_text(
        json.dumps(_valid_manifest(plane="plane1")), encoding="utf-8"
    )
    (module_dir / "client.py").write_text("import plane2.something\n", encoding="utf-8")

    pipeline = _pipeline(tmp_path)
    report = pipeline.run(module_dir)

    assert report.manifest_valid is True
    assert report.is_pass() is False
    assert any(v["rule_id"] == "M1-P1" for v in report.m1_violations)


def test_run_m2_port_registry_violation(tmp_path: Path) -> None:
    module_dir = tmp_path / "unknown_port"
    module_dir.mkdir()
    (module_dir / "egregore-module.json").write_text(
        json.dumps(_valid_manifest(ports={"implements": ["UnknownPort"]})),
        encoding="utf-8",
    )
    (module_dir / "__init__.py").write_text("x = 1\n", encoding="utf-8")

    pipeline = _pipeline(tmp_path)
    report = pipeline.run(module_dir)

    assert report.manifest_valid is True
    assert report.is_pass() is False
    assert any(
        v["rule_id"] == "M2-PORT" and "UnknownPort" in v["message"]
        for v in report.m2_violations
        if v.get("severity") == "error"
    )
