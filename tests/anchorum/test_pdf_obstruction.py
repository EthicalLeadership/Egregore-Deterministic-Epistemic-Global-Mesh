"""Tests for the stdlib-only PDF Obstruction Detector."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from anchorum.forensic.core.document.pdf_obstruction import (
    PdfParseError,
    PdfStructure,
    SignalAudit,
    _score_obstruction,
    detect_obstruction,
)


def test_detect_obstruction_on_empty_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "empty.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(str(pdf_path))

    ref = detect_obstruction(source=pdf_path, case_id="C1", operator="tester")
    assert ref.audit_path.exists()
    report = __import__("json").loads(ref.audit_path.read_text())
    assert report["case_id"] == "C1"
    assert "original_hash" in report
    assert 0 <= report["obstruction_score"] <= 100


def test_detect_obstruction_accepts_bytes(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(str(pdf_path))
    data = pdf_path.read_bytes()

    ref = detect_obstruction(source=data, case_id="C2", operator="tester")
    assert ref.audit_path.exists()


def test_detect_obstruction_rejects_non_pdf() -> None:
    with pytest.raises(PdfParseError):
        detect_obstruction(source=b"not a pdf", case_id="C3", operator="tester")


def test_score_heavy_restrictions() -> None:
    structure = PdfStructure(
        is_encrypted=True,
        permissions=0,  # all active-low bits cleared = all restrictions active
    )
    score, signals = _score_obstruction(structure)
    assert score >= 30
    assert any(s.name == "heavy_restrictions" and s.triggered for s in signals)


def test_score_rasterized() -> None:
    structure = PdfStructure(page_count=1, text_objects_detected=0, is_rasterized=True)
    score, signals = _score_obstruction(structure)
    assert any(s.name == "rasterized" and s.triggered for s in signals)


def test_signal_audit_immutable() -> None:
    s = SignalAudit("x", "desc", True, 5)
    with pytest.raises(AttributeError):
        s.triggered = False  # type: ignore[misc]
