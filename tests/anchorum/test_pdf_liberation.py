"""Tests for CUSTOM-008 PDF Liberation Engine."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from anchorum.forensic.core.document.pdf_liberation import liberate
from anchorum.forensic.core.provenance import (
    ZarcEventType,
    clear_events,
    emitted_events,
)


@pytest.fixture(autouse=True)
def _clear_events() -> None:
    clear_events()


def _make_readonly(path: Path) -> None:
    path.chmod(0o444)


def _make_encrypted_pdf(path: Path, owner_password: str) -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(
        str(path),
        encryption=pikepdf.Encryption(owner=owner_password, R=6),
    )


def test_liberate_removes_encryption(tmp_path: Path) -> None:
    locked = tmp_path / "locked.pdf"
    _make_encrypted_pdf(locked, "owner123")
    _make_readonly(locked)

    out_dir = tmp_path / "out"
    manifest = liberate(
        input_pdf=locked,
        password="owner123",
        output_dir=out_dir,
        case_id="CASE-001",
        operator="tester",
    )

    assert "original_hash" in manifest
    assert "clean_hash" in manifest
    assert manifest["original_hash"] != manifest["clean_hash"]
    assert manifest["encryption_removed"] is True
    assert Path(manifest["clean_path"]).exists()
    assert Path(manifest["manifest_path"]).exists()

    # Verify clean PDF is no longer encrypted
    with pikepdf.open(manifest["clean_path"]) as clean:
        assert not clean.is_encrypted

    events = emitted_events()
    assert len(events) == 1
    assert events[0]["event_type"] == ZarcEventType.PDF_LIBERATION.value
    assert events[0]["case_id"] == "CASE-001"


def test_liberate_bad_password_raises(tmp_path: Path) -> None:
    locked = tmp_path / "locked.pdf"
    _make_encrypted_pdf(locked, "owner123")
    _make_readonly(locked)

    with pytest.raises(ValueError, match="bad password"):
        liberate(
            input_pdf=locked,
            password="wrong",
            output_dir=tmp_path / "out",
            case_id="CASE-002",
            operator="tester",
        )


def test_liberate_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    with pytest.raises(FileNotFoundError):
        liberate(
            input_pdf=missing,
            password=None,
            output_dir=tmp_path / "out",
            case_id="CASE-003",
            operator="tester",
        )


def test_liberate_writable_original_raises(tmp_path: Path) -> None:
    pdf = tmp_path / "writable.pdf"
    test_pdf = pikepdf.new()
    test_pdf.add_blank_page()
    test_pdf.save(str(pdf))
    # File is writable by default
    with pytest.raises(PermissionError, match="read-only"):
        liberate(
            input_pdf=pdf,
            password=None,
            output_dir=tmp_path / "out",
            case_id="CASE-004",
            operator="tester",
        )
