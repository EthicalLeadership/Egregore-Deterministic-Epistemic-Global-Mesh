"""Tests for ANCHORUM input-size and resource guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from anchorum.forensic.core.document.ocr_confidence import analyze_ocr
from anchorum.forensic.core.document.pdf_liberation import liberate
from anchorum.forensic.core.document.steganography_detector import detect_steganography
from anchorum.forensic.core.timeline_fusion import (
    FileMetadataSource,
    JsonFileRevisionSource,
)
from anchorum.forensic.core.validation import MAX_INPUT_BYTES, validate_input_size


def _make_sparse_file(path: Path, size: int) -> Path:
    """Create a sparse file of the requested size without writing data."""
    with open(path, "wb") as f:
        f.truncate(size)
    return path


def test_validate_input_size_rejects_oversized_bytes() -> None:
    with pytest.raises(ValueError, match="exceeds maximum"):
        validate_input_size(b"x" * 11, max_bytes=10, label="payload")


def test_validate_input_size_accepts_undersized_bytes() -> None:
    validate_input_size(b"x" * 10, max_bytes=10, label="payload")


def test_liberate_rejects_oversized_pdf(tmp_path: Path) -> None:
    pdf = _make_sparse_file(tmp_path / "big.pdf", MAX_INPUT_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds maximum"):
        liberate(
            input_pdf=pdf,
            password=None,
            output_dir=tmp_path / "out",
            case_id="C1",
            operator="test",
        )


def test_analyze_ocr_rejects_oversized_file(tmp_path: Path) -> None:
    img = _make_sparse_file(tmp_path / "big.png", MAX_INPUT_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds maximum"):
        analyze_ocr(source=img, case_id="C1", operator="test")


def test_detect_steganography_rejects_oversized_file(tmp_path: Path) -> None:
    img = _make_sparse_file(tmp_path / "big.png", MAX_INPUT_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds maximum"):
        detect_steganography(source=img, case_id="C1", operator="test")


def test_file_metadata_source_rejects_oversized(tmp_path: Path) -> None:
    f = _make_sparse_file(tmp_path / "big.txt", MAX_INPUT_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds maximum"):
        FileMetadataSource(f)


def test_json_revision_source_rejects_oversized(tmp_path: Path) -> None:
    f = _make_sparse_file(tmp_path / "big.json", MAX_INPUT_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds maximum"):
        JsonFileRevisionSource(f)
