"""Tests for ANCHORUM analysis module."""

from __future__ import annotations

from modules.anchorum.analysis import detect_manipulation


def test_detect_pdf_javascript() -> None:
    pdf = b"%PDF-1.4\n<< /JavaScript /JS (alert(1)) >>"
    report = detect_manipulation(pdf, evidence_id="E-1")
    assert report["evidence_id"] == "E-1"
    assert any(f["type"] == "embedded_javascript" for f in report["findings"])
    assert report["score"] > 0


def test_detect_email_sender_mismatch() -> None:
    email = (
        b"From: alice@example.com\n"
        b"Sender: bob@example.com\n"
        b"Date: Mon, 01 Jan 2024 12:00:00 +0000\n\n"
        b"body"
    )
    report = detect_manipulation(email, evidence_id="E-2")
    assert any(f["type"] == "sender_mismatch" for f in report["findings"])
