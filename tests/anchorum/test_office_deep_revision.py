"""Tests for ANCHORUM Office Deep Revision Recovery (CUSTOM-001)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from anchorum.forensic.core.document.office_deep_revision import (
    DocumentRevisionReport,
    recover_revisions,
)


def _build_docx(path: Path, document_xml: str, comments_xml: str | None = None) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
        if comments_xml:
            zf.writestr("word/comments.xml", comments_xml)
        zf.writestr("[Content_Types].xml", "")


@pytest.fixture
def sample_docx(tmp_path: Path) -> Path:
    """Build a minimal docx with a tracked insertion and a comment."""
    docx_path = tmp_path / "sample.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:t>Hello </w:t></w:r>
      <w:ins w:id="1" w:author="alice" w:date="2025-01-01T12:00:00Z">
        <w:r><w:t>world</w:t></w:r>
      </w:ins>
    </w:p>
  </w:body>
</w:document>"""
    comments_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:comment w:id="2" w:author="bob" w:date="2025-01-02T10:00:00Z">
    <w:p><w:r><w:t>Review comment</w:t></w:r></w:p>
  </w:comment>
</w:comments>"""
    _build_docx(docx_path, document_xml, comments_xml)
    return docx_path


def test_recover_revisions_finds_insertion_and_comment(sample_docx: Path) -> None:
    ref = recover_revisions(source=sample_docx, case_id="CASE-ODR", operator="tester")
    assert ref.audit_path.exists()
    report = json.loads(ref.audit_path.read_text())

    assert report["document_type"] == ".docx"
    assert report["case_id"] == "CASE-ODR"
    assert any(
        r["revision_type"] == "insertion" and r["author"] == "alice"
        for r in report["revision_history"]
    )
    assert any(
        c["author"] == "bob" and c["text"] == "Review comment"
        for c in report["comments"]
    )


def test_recover_revisions_accepts_bytes(sample_docx: Path) -> None:
    data = sample_docx.read_bytes()
    ref = recover_revisions(source=data, case_id="CASE-BYTES", operator="tester")
    assert ref.audit_path.exists()
    report = json.loads(ref.audit_path.read_text())
    assert report["document_type"] == "unknown"
    assert any(r["revision_type"] == "insertion" for r in report["revision_history"])


def test_recover_revisions_bad_zip(tmp_path: Path) -> None:
    bad = tmp_path / "not_a_docx.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="Not a valid ZIP archive"):
        recover_revisions(source=bad, case_id="CASE-BAD", operator="tester")


def test_recover_revisions_missing_ooxml_parts(tmp_path: Path) -> None:
    empty_zip = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty_zip, "w") as zf:
        zf.writestr("readme.txt", b"hello")
    with pytest.raises(ValueError, match="No recognizable OOXML parts"):
        recover_revisions(source=empty_zip, case_id="CASE-EMPTY", operator="tester")


def test_document_revision_report_is_immutable() -> None:
    report = DocumentRevisionReport(
        original_hash="a" * 64,
        document_type=".docx",
        revision_history=(),
        comments=(),
        previous_versions=(),
        metadata={},
        author_map={},
    )
    with pytest.raises(AttributeError):
        report.document_type = ".xlsx"  # type: ignore[misc]
