"""Tests for M1/M2 governance checks."""

from __future__ import annotations

import ast
from typing import Any

from egregore.pipeline.governance import run_m1, run_m2


def _manifest(**overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "name": "mod",
        "plane": "plane1",
        "layer": "kernel",
        "dependencies": [],
        "capabilities": {},
    }
    manifest.update(overrides)
    return manifest


def _asts(source: str) -> list[ast.Module]:
    return [ast.parse(source)]


def test_m1_plane1_imports_plane2_is_error() -> None:
    manifest = _manifest(plane="plane1", layer="kernel")
    violations = run_m1(
        _asts("import plane2.secret"),
        manifest,
        plane1_ports=[],
        concrete_infrastructure=[],
    )
    assert any(v["rule_id"] == "M1-P1" for v in violations)


def test_m1_plane2_requires_declared_port() -> None:
    manifest = _manifest(plane="plane2", layer="interface")
    violations = run_m1(
        _asts("import plane1.telemetry"),
        manifest,
        plane1_ports=["plane1.telemetry"],
        concrete_infrastructure=[],
    )
    assert not any(v["rule_id"] == "M1-P2-INT" for v in violations)


def test_m1_plane2_undeclared_port_is_error() -> None:
    manifest = _manifest(plane="plane2", layer="interface")
    violations = run_m1(
        _asts("import plane1.internal"),
        manifest,
        plane1_ports=["plane1.telemetry"],
        concrete_infrastructure=[],
    )
    assert any(v["rule_id"] == "M1-P2-INT" for v in violations)


def test_m1_interface_must_not_import_concrete_infrastructure() -> None:
    manifest = _manifest(plane="plane2", layer="interface")
    violations = run_m1(
        _asts("import plane1.database"),
        manifest,
        plane1_ports=["plane1.database"],
        concrete_infrastructure=["plane1.database"],
    )
    assert any(v["rule_id"] == "M1-IFACE" for v in violations)


def test_m2_undeclared_internal_import() -> None:
    manifest = _manifest(dependencies=[])
    violations = run_m2(
        _asts("import plane1.dep"),
        manifest,
        port_registry=[],
    )
    assert any(v["rule_id"] == "M2-DEP" for v in violations)


def test_m2_floating_version_is_error() -> None:
    manifest = _manifest(
        dependencies=[
            {"name": "plane1.dep", "version": "latest", "hash": "sha256:" + "a" * 64}
        ]
    )
    violations = run_m2(
        _asts("import plane1.dep"),
        manifest,
        port_registry=[],
    )
    assert any(v["rule_id"] == "M2-DEP-FLOAT" for v in violations)


def test_m2_missing_hash_is_error() -> None:
    manifest = _manifest(
        dependencies=[{"name": "plane1.dep", "version": "1.2.3", "hash": ""}]
    )
    violations = run_m2(
        _asts("import plane1.dep"),
        manifest,
        port_registry=[],
    )
    assert any(v["rule_id"] == "M2-DEP-FLOAT" for v in violations)


def test_m2_declared_pinned_dependency_passes() -> None:
    manifest = _manifest(
        dependencies=[
            {"name": "plane1.dep", "version": "1.2.3", "hash": "sha256:" + "a" * 64}
        ]
    )
    violations = run_m2(
        _asts("import plane1.dep"),
        manifest,
        port_registry=[],
    )
    assert not any(v.get("severity") == "error" for v in violations)


def test_m2_missing_capability_on_plane1_is_error() -> None:
    manifest = _manifest(plane="plane1", capabilities={})
    violations = run_m2(
        _asts("open('file.txt')"),
        manifest,
        port_registry=[],
    )
    assert any(
        v["rule_id"] == "M2-CAP" and v.get("severity") == "error" for v in violations
    )


def test_m2_declared_capability_passes() -> None:
    manifest = _manifest(
        plane="plane1",
        capabilities={"read": ["file"], "write": ["file"]},
    )
    violations = run_m2(
        _asts("open('file.txt')"),
        manifest,
        port_registry=[],
    )
    assert not any(v["rule_id"] == "M2-CAP" for v in violations)


def test_m2_unknown_port_is_error() -> None:
    manifest = _manifest(ports={"implements": ["UnknownPort"]})
    violations = run_m2(
        _asts("x = 1"),
        manifest,
        port_registry=["KnownPort"],
    )
    assert any(
        v["rule_id"] == "M2-PORT" and "UnknownPort" in v["message"] for v in violations
    )
