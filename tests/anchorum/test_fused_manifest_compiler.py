"""Tests for ANCHORUM Fused Manifest Compiler."""

from __future__ import annotations

from pathlib import Path

from anchorum.forensic.core.document.fused_manifest_compiler import (
    compile_fused_manifest,
)
from anchorum.forensic.core.document.hidden_layer_detection import HiddenLayerVerdict
from anchorum.forensic.core.document.integrity_attestation import IntegrityAttestation
from anchorum.forensic.core.document.metadata_extraction import MetadataPlane
from anchorum.forensic.core.document.pdf_pharos_engine import DocumentVerdict
from anchorum.forensic.core.document.signature_pharos import SignatureVerdict


def test_compile_manifest(tmp_path: Path) -> None:
    classification = DocumentVerdict(
        input_path=tmp_path / "file.pdf",
        file_type="pdf",
        is_redacted=True,
        is_scanned=False,
        page_count=2,
        classification_confidence=0.92,
    )
    metadata = MetadataPlane(
        producer="TestProducer",
        creator="TestCreator",
        created="2025-01-01",
        modified="2025-01-02",
    )
    signature = SignatureVerdict()
    hidden = HiddenLayerVerdict()
    integrity = IntegrityAttestation(
        file_hash="a" * 64,
        algorithm="sha256",
        is_valid=True,
    )

    manifest = compile_fused_manifest(
        anchorum_id="ANCH-123",
        input_path=tmp_path / "file.pdf",
        classification=classification,
        metadata=metadata,
        signature=signature,
        hidden_layers=hidden,
        integrity=integrity,
    )

    assert manifest.anchorum_id == "ANCH-123"
    assert "redacted" in manifest.tags
    assert "signature_issue" not in manifest.tags
    assert "integrity_failure" not in manifest.tags

    out = tmp_path / "manifest.json"
    manifest.write_json(out)
    assert out.exists()
    assert "anchorum_id" in out.read_text()
