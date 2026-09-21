"""Tests for ANCHORUM batch extraction CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anchorum.forensic.core.cli.batch_extract import main
from anchorum.forensic.core.provenance import clear_events

try:
    from PIL import Image

    PILLOW_AVAILABLE = True
except Exception:
    PILLOW_AVAILABLE = False


def _build_minimal_pdf() -> bytes:
    return b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [] /Count 0 >>
endobj
xref
0 3
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
trailer
<< /Size 3 /Root 1 0 R >>
startxref
114
%%EOF
"""


def _build_minimal_email() -> bytes:
    return b"""From: sender@example.com
To: receiver@example.com
Subject: Batch Test
Date: Thu, 15 Jun 2023 10:30:00 +0000

https://example.com
"""


def _build_minimal_image() -> bytes:
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow not installed")
    import io

    img = Image.new("RGB", (20, 20), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_batch_extract_cli(tmp_path: Path) -> None:
    clear_events()

    root = tmp_path / "evidence"
    root.mkdir()
    (root / "doc1.pdf").write_bytes(_build_minimal_pdf())
    (root / "mail1.eml").write_bytes(_build_minimal_email())
    (root / "pic1.png").write_bytes(_build_minimal_image())
    # Noise file that should be skipped
    (root / "noise.txt").write_text("not evidence")

    output = tmp_path / "out.jsonl"
    summary = tmp_path / "summary.json"

    rc = main(
        [
            str(root),
            "--case-id",
            "CASE-BATCH",
            "--operator",
            "tester",
            "--output",
            str(output),
            "--summary",
            str(summary),
            "--no-enforce-readonly",
        ]
    )

    assert rc == 0
    assert output.exists()
    assert summary.exists()

    lines = output.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3

    first = json.loads(lines[0])
    assert "container_type" in first
    assert "source_path" in first

    summary_data = json.loads(summary.read_text(encoding="utf-8"))
    assert summary_data["artifact_count"] == 3
    assert summary_data["error_count"] == 0
    assert summary_data["unique_urls"] == 1
