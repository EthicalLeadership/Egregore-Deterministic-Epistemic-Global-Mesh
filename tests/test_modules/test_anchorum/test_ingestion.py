"""Tests for ANCHORUM ingestion module."""

from __future__ import annotations

from modules.anchorum.ingestion import detect_container, extract_metadata, hash_bytes
from modules.anchorum.ingestion.filetypes import ContainerType


def test_detect_container_pdf() -> None:
    assert detect_container(b"%PDF-1.4\n") == ContainerType.PDF


def test_detect_container_email() -> None:
    assert detect_container(b"From: alice@example.com\n\nbody") == ContainerType.EMAIL


def test_extract_metadata_pdf() -> None:
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Producer (Test) >>\nendobj\n"
    metadata = extract_metadata(pdf_bytes, filename_hint="test.pdf")
    assert metadata["container_type"] == "pdf"
    # pikepdf may or may not be installed in the test env; ensure structure either way
    assert "raw" in metadata


def test_hash_bytes() -> None:
    assert hash_bytes(b"hello") == hash_bytes(b"hello")
    assert hash_bytes(b"hello") != hash_bytes(b"world")
