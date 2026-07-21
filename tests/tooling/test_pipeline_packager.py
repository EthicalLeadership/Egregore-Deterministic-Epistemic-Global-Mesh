"""Tests for pipeline packager and .zarc bundle creation."""

from __future__ import annotations

from pathlib import Path

from egregore.kernel.ed25519_signer import generate_signing_key
from egregore.kernel.provenance import Provenance
from egregore.tooling.pipeline_models import AuditReport, Cbi0Block, ModuleManifest
from egregore.tooling.pipeline_packager import package_module


def test_package_module_writes_artifacts_and_verifies(tmp_path: Path) -> None:
    key = generate_signing_key()
    manifest = ModuleManifest(
        module_id="egregore.test_module",
        version="0.1.0",
        cbi0=Cbi0Block(m1_plane="shared", m1_layer="shared"),
    )
    report = AuditReport(
        module_id="egregore.test_module",
        timestamp_ns=1,
        pipeline_class="standard",
        m1={"status": "PASS", "violations": []},
        m2={"status": "PASS", "violations": []},
        m3={"status": "NOT_VERIFIED"},
        m4={"status": "DIVERGED"},
        m5={"status": "NOT_ENFORCED"},
    )

    manifest_path, report_path, zarc_path = package_module(
        out_dir=tmp_path,
        module_id="egregore.test_module",
        manifest=manifest,
        report=report,
        signing_key_hex=key,
        timestamp_ns=1,
    )

    assert manifest_path.exists()
    assert report_path.exists()
    assert zarc_path.exists()

    prov = Provenance(zarc_path, signing_key_hex=key)
    assert prov.verify_chain()
    entries = list(prov.iter_entries())
    assert len(entries) == 1
    assert entries[0].engine == "module_pipeline"
    assert entries[0].event == "module_audited"
    assert entries[0].payload["module_id"] == "egregore.test_module"
