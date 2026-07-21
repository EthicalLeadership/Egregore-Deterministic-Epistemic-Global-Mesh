"""Tests for ANCHORUM PDF forensic planes."""

from __future__ import annotations

import json
from pathlib import Path

import pikepdf
import pytest

from anchorum.forensic.core.document.hidden_layer_detection import HiddenLayerDetector
from anchorum.forensic.core.document.integrity_attestation import IntegrityAttestor
from anchorum.forensic.core.document.metadata_extraction import MetadataExtractor
from anchorum.forensic.core.document.pdf_obstruction import detect_obstruction
from anchorum.forensic.core.document.pdf_pharos_engine import PdfPharosEngine
from anchorum.forensic.core.document.signature_pharos import SignaturePharos


@pytest.fixture
def empty_pdf(tmp_path: Path) -> Path:
    pdf_path = tmp_path / "empty.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(612, 792))
        pdf.save(str(pdf_path))
    return pdf_path


def test_pharos_classifies_empty_pdf(empty_pdf: Path) -> None:
    verdict = PdfPharosEngine().classify(empty_pdf)
    assert verdict.file_type == "pdf"
    assert verdict.page_count == 1
    assert verdict.classification_confidence > 0.5
    assert not verdict.is_redacted


def test_metadata_extractor_empty_pdf(empty_pdf: Path) -> None:
    meta = MetadataExtractor().extract(empty_pdf)
    assert meta.encrypted is False
    assert meta.exiftool_available in (True, False)


def test_signature_empty_pdf(empty_pdf: Path) -> None:
    sig = SignaturePharos().inspect(empty_pdf)
    assert sig.signed_count == 0
    assert sig.unsigned_count == 0


def test_hidden_layers_empty_pdf(empty_pdf: Path) -> None:
    hidden = HiddenLayerDetector().inspect(empty_pdf)
    assert hidden.total_hidden_layers == 0


def test_integrity_attestation(empty_pdf: Path) -> None:
    att = IntegrityAttestor().attest(empty_pdf)
    assert att.is_valid
    assert att.algorithm == "sha256"
    assert len(att.file_hash) == 64


def test_obstruction_empty_pdf(empty_pdf: Path) -> None:
    ref = detect_obstruction(source=empty_pdf, case_id="CASE-OB", operator="tester")
    assert ref.audit_path.exists()
    report = json.loads(ref.audit_path.read_text())
    assert 0 <= report["obstruction_score"] <= 100
    assert report["case_id"] == "CASE-OB"
