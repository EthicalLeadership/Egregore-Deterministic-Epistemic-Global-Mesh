"""Tests for the build-time CBI-0 module pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from egregore.tooling.module_pipeline import (
    AstImportAnalyzer,
    CapabilityScanner,
    M1Checker,
    M2Checker,
    M3Checker,
    M5CellAwarenessChecker,
    ModuleManifest,
    ModulePipelineRunner,
    PlaneLayerClassifier,
)


@pytest.fixture
def classifier() -> PlaneLayerClassifier:
    return PlaneLayerClassifier()


@pytest.fixture
def m1_checker(classifier: PlaneLayerClassifier) -> M1Checker:
    return M1Checker(classifier, {})


@pytest.fixture
def m2_checker() -> M2Checker:
    return M2Checker("0.6.0")


class TestPlaneLayerClassifier:
    def test_plane1_layers(self, classifier: PlaneLayerClassifier) -> None:
        assert classifier.classify("egregore.domain.models") == ("plane1", "domain")
        assert classifier.classify("egregore.application.inference_service") == (
            "plane1",
            "application",
        )
        assert classifier.classify("egregore.governance.cbi0_governance") == (
            "plane1",
            "governance",
        )

    def test_plane2_layers(self, classifier: PlaneLayerClassifier) -> None:
        assert classifier.classify("egregore.interface.bootstrap") == (
            "plane2",
            "interface",
        )
        assert classifier.classify("egregore.infrastructure.local_model_catalog") == (
            "plane2",
            "infrastructure",
        )

    def test_cross_layers(self, classifier: PlaneLayerClassifier) -> None:
        assert classifier.classify("egregore.shared.canonical") == ("shared", "shared")


class TestAstImportAnalyzer:
    def test_egregore_imports(self) -> None:
        source = """
from egregore.domain import models
import egregore.infrastructure.local_model_catalog
from typing import Any
"""
        analyzer = AstImportAnalyzer(source, "egregore.application.foo")
        assert analyzer.egregore_imports() == {
            "egregore.domain",
            "egregore.infrastructure",
        }

    def test_resolves_to(self) -> None:
        source = "import egregore.interface.constraint_binding_ports"
        analyzer = AstImportAnalyzer(source, "egregore.interface.foo")
        assert analyzer.resolves_to("egregore.interface.constraint_binding_ports")


class TestCapabilityScanner:
    def test_detects_read_and_execute(self) -> None:
        source = """
with open("file.txt") as f:
    data = f.read()

import subprocess
subprocess.run(["ls"])
"""
        scanner = CapabilityScanner()
        caps = scanner.scan(source)
        assert caps["read"]
        assert caps["execute"]
        assert not caps["write"]
        assert not caps["network"]


class TestM1Checker:
    def test_domain_importing_infrastructure_fails(self, m1_checker: M1Checker) -> None:
        result = m1_checker.check(
            "egregore.domain.foo",
            "domain",
            {"egregore.infrastructure.local_model_catalog"},
        )
        assert result.status == "FAIL"
        assert any(v.rule == "M1-LAYER" for v in result.violations)

    def test_interface_importing_kernel_fails(self, m1_checker: M1Checker) -> None:
        result = m1_checker.check(
            "egregore.interface.foo",
            "interface",
            {"egregore.kernel.scheduler"},
        )
        assert result.status == "FAIL"
        assert any(v.rule == "M1-LAYER" for v in result.violations)

    def test_application_importing_allowed_domain_passes(
        self, m1_checker: M1Checker
    ) -> None:
        result = m1_checker.check(
            "egregore.application.cbi_0_orchestrated_executor",
            "application",
            {"egregore.domain.models"},
        )
        assert result.status == "PASS"

    def test_interface_importing_infrastructure_fails(
        self, m1_checker: M1Checker
    ) -> None:
        result = m1_checker.check(
            "egregore.interface.foo",
            "interface",
            {"egregore.infrastructure.local_model_catalog"},
        )
        assert result.status == "FAIL"
        assert any(v.rule == "M1-IFACE" for v in result.violations)


class TestM2Checker:
    def test_missing_dependency(self, m2_checker: M2Checker) -> None:
        manifest = ModuleManifest(
            module_id="egregore.domain.foo",
            cbi0={"m1_plane": "plane1", "m1_layer": "domain"},
        )
        result = m2_checker.check(
            "egregore.domain.foo",
            "domain",
            {"egregore.infrastructure"},
            {},
            manifest,
        )
        assert result.status == "FAIL"
        assert any(v.rule == "M2-DEP" for v in result.violations)

    def test_missing_capability_warns_plane2(self, m2_checker: M2Checker) -> None:
        manifest = ModuleManifest(
            module_id="egregore.interface.foo",
            cbi0={"m1_plane": "plane2", "m1_layer": "interface"},
        )
        result = m2_checker.check(
            "egregore.interface.foo",
            "interface",
            set(),
            {"read": ["line 1: open('x')"]},
            manifest,
        )
        assert result.status == "WARN"
        assert any(v.rule == "M2-CAP" for v in result.violations)


class TestM3Checker:
    def test_not_enforced_when_not_terminal(self) -> None:
        manifest = ModuleManifest(
            module_id="egregore.application.foo",
            cbi0={"m1_plane": "plane1", "m1_layer": "application"},
        )
        result = M3Checker().check([(Path("foo.py"), "x = 1")], manifest)
        assert result.status == "NOT_ENFORCED"
        assert result.metadata["terminal"] is False

    def test_terminal_with_clean_source_passes(self) -> None:
        manifest = ModuleManifest(
            module_id="egregore.application.foo",
            cbi0={
                "m1_plane": "plane1",
                "m1_layer": "application",
                "m3": {
                    "terminal": True,
                    "decom_manifest": {
                        "dependencies": ["egregore.interface.foo"],
                        "procedure": "docs/decom/egregore.application.foo.md",
                        "test_log": "logs/decom/egregore.application.foo.log",
                        "attestation": {
                            "signature": "sig",
                            "signer_id": "dsb-chair",
                            "timestamp": "2026-07-19T00:00:00Z",
                        },
                    },
                },
            },
        )
        result = M3Checker().check([(Path("foo.py"), "x = 1")], manifest)
        assert result.status == "PASS"
        assert result.metadata["terminal"] is True
        assert result.metadata["attested"] is True

    def test_terminal_with_del_warns(self) -> None:
        manifest = ModuleManifest(
            module_id="egregore.application.foo",
            cbi0={
                "m1_plane": "plane1",
                "m1_layer": "application",
                "m3": {
                    "terminal": True,
                    "decom_manifest": {
                        "attestation": {
                            "bootstrap_waiver": "BOOTSTRAP-2026-001",
                        },
                    },
                },
            },
        )
        result = M3Checker().check(
            [(Path("foo.py"), "class A:\n    def __del__(self): pass")], manifest
        )
        assert result.status == "WARN"
        assert any(v.rule == "M3-TERM" for v in result.violations)
        assert result.metadata["attested"] is True

    def test_terminal_with_atexit_warns(self) -> None:
        manifest = ModuleManifest(
            module_id="egregore.application.foo",
            cbi0={
                "m1_plane": "plane1",
                "m1_layer": "application",
                "m3": {
                    "terminal": True,
                    "decom_manifest": {
                        "attestation": {
                            "bootstrap_waiver": "BOOTSTRAP-2026-001",
                        },
                    },
                },
            },
        )
        result = M3Checker().check(
            [(Path("foo.py"), "import atexit\natexit.register(cleanup)")], manifest
        )
        assert result.status == "WARN"
        assert any(v.rule == "M3-TERM" for v in result.violations)
        assert result.metadata["attested"] is True

    def test_terminal_without_decom_manifest_fails(self) -> None:
        manifest = ModuleManifest(
            module_id="egregore.application.foo",
            cbi0={
                "m1_plane": "plane1",
                "m1_layer": "application",
                "m3": {"terminal": True},
            },
        )
        result = M3Checker().check([(Path("foo.py"), "x = 1")], manifest)
        assert result.status == "FAIL"
        assert any(v.rule == "M3-NO-DECOM" for v in result.violations)
        assert result.metadata["attested"] is False

    def test_terminal_without_attestation_fails(self) -> None:
        manifest = ModuleManifest(
            module_id="egregore.application.foo",
            cbi0={
                "m1_plane": "plane1",
                "m1_layer": "application",
                "m3": {
                    "terminal": True,
                    "decom_manifest": {"dependencies": ["egregore.interface.foo"]},
                },
            },
        )
        result = M3Checker().check([(Path("foo.py"), "x = 1")], manifest)
        assert result.status == "FAIL"
        assert any(v.rule == "M3-NO-ATTESTATION" for v in result.violations)
        assert result.metadata["attested"] is False


class TestM5CellAwarenessChecker:
    def test_no_model_agent_usage(self) -> None:
        manifest = ModuleManifest(module_id="egregore.domain.foo")
        result = M5CellAwarenessChecker().check(
            {"egregore.domain.models"}, [], manifest
        )
        assert result.status == "NOT_ENFORCED"
        assert not result.violations

    def test_model_agent_usage_without_cell_warns(self) -> None:
        manifest = ModuleManifest(module_id="egregore.interface.foo")
        result = M5CellAwarenessChecker().check(
            {"egregore.application.agent_registry"}, [], manifest
        )
        assert result.status == "WARN"
        assert any(v.rule == "M5-CELL" for v in result.violations)

    def test_model_agent_usage_with_cell_passes(self) -> None:
        manifest = ModuleManifest(
            module_id="egregore.interface.foo", cell="anchorum_forensic"
        )
        result = M5CellAwarenessChecker().check(
            {"egregore.application.agent_registry"}, [], manifest
        )
        assert result.status == "PASS"
        assert not result.violations

    def test_non_zero_temperature_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_spec = {
            "cell_id": "anchorum_forensic",
            "pipeline": {
                "stages": [
                    {"stage_id": "generate", "model": "qwen", "temperature": 0.7},
                ],
            },
            "verification": {"rules": [{"rule_id": "r1"}]},
            "moral_compliance": {"egregore_laws": [{"law_id": "l1"}]},
        }
        monkeypatch.setattr(
            "egregore.tooling.pipeline_checkers._load_cell_spec",
            lambda _cell_id: fake_spec,
        )
        manifest = ModuleManifest(
            module_id="egregore.interface.foo", cell="anchorum_forensic"
        )
        result = M5CellAwarenessChecker().check(
            {"egregore.application.agent_registry"}, [], manifest
        )
        assert result.status == "WARN"
        assert any(v.rule == "M5-DET" for v in result.violations)

    def test_unseeded_random_in_source_warns(self) -> None:
        manifest = ModuleManifest(
            module_id="egregore.interface.foo", cell="anchorum_forensic"
        )
        result = M5CellAwarenessChecker().check(
            {"egregore.application.agent_registry"},
            [(Path("foo.py"), "import random\nx = random.random()")],
            manifest,
        )
        assert result.status == "WARN"
        assert any(v.rule == "M5-DET" for v in result.violations)

    def test_missing_epistemic_markers_without_cell_warns(self) -> None:
        manifest = ModuleManifest(module_id="egregore.interface.foo")
        result = M5CellAwarenessChecker().check(
            {"egregore.application.agent_registry"},
            [(Path("foo.py"), "x = 1")],
            manifest,
        )
        assert result.status == "WARN"
        assert any(v.rule == "M5-EPI" for v in result.violations)

    def test_cell_missing_verification_rules_warns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_spec = {
            "cell_id": "anchorum_forensic",
            "pipeline": {"stages": []},
            "verification": {"rules": []},
            "moral_compliance": {"egregore_laws": [{"law_id": "l1"}]},
        }
        monkeypatch.setattr(
            "egregore.tooling.pipeline_checkers._load_cell_spec",
            lambda _cell_id: fake_spec,
        )
        manifest = ModuleManifest(
            module_id="egregore.interface.foo", cell="anchorum_forensic"
        )
        result = M5CellAwarenessChecker().check(
            {"egregore.application.agent_registry"}, [], manifest
        )
        assert result.status == "WARN"
        assert any(v.rule == "M5-EPI" for v in result.violations)


class TestModulePipelineRunner:
    def test_run_on_shared_module(self) -> None:
        runner = ModulePipelineRunner(pipeline_class="standard")
        report = runner.run(Path("src/egregore/shared"))
        assert report.m1["status"] == "PASS"

    def test_fast_pipeline_skips_m2_and_m5(self) -> None:
        runner = ModulePipelineRunner(pipeline_class="fast")
        report = runner.run(Path("src/egregore/shared"))
        assert report.m1["status"] == "PASS"
        assert report.m2["status"] == "NOT_VERIFIED"
        assert report.m3["status"] == "NOT_VERIFIED"
        assert report.m5["status"] == "NOT_ENFORCED"

    def test_generate_manifest_roundtrip(self, tmp_path: Path) -> None:
        runner = ModulePipelineRunner()
        manifest = runner.generate_manifest(Path("src/egregore/shared"))
        assert manifest.module_id == "egregore.shared"
        assert manifest.cbi0.m1_layer == "shared"

        manifest_path = tmp_path / "egregore-module.json"
        manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
        loaded = ModuleManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        assert loaded.module_id == manifest.module_id
