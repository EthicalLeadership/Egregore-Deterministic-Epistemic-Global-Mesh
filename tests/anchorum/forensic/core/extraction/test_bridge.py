"""Tests for ANCHORUM ingestion-extraction bridge."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from anchorum.forensic.core.extraction.bridge import extract_from_artifact
from anchorum.forensic.core.ingestion import ingest_artifact

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


def _build_minimal_docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '  <Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "  <w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )
    return buf.getvalue()


def _build_minimal_email() -> bytes:
    return b"""From: sender@example.com
To: receiver@example.com
Subject: Test
Date: Thu, 15 Jun 2023 10:30:00 +0000

Body text.
"""


def _build_minimal_image() -> bytes:
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow not installed")
    img = Image.new("RGB", (10, 10), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _write_file(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_bridge_pdf(tmp_path: Path) -> None:
    path = _write_file(tmp_path, "test.pdf", _build_minimal_pdf())
    artifact = ingest_artifact(path, "CASE-001", "tester", enforce_readonly=False)
    extracted = extract_from_artifact(artifact)

    assert extracted.artifact_id == artifact.artifact_id
    assert extracted.plane_container is not None


def test_bridge_ooxml(tmp_path: Path) -> None:
    path = _write_file(tmp_path, "test.docx", _build_minimal_docx())
    artifact = ingest_artifact(path, "CASE-001", "tester", enforce_readonly=False)
    extracted = extract_from_artifact(artifact)

    assert extracted.plane_container is not None


def test_bridge_email(tmp_path: Path) -> None:
    path = _write_file(tmp_path, "test.eml", _build_minimal_email())
    artifact = ingest_artifact(path, "CASE-001", "tester", enforce_readonly=False)
    extracted = extract_from_artifact(artifact)

    assert extracted.plane_container is not None
    assert extracted.plane_container.from_addr == "sender@example.com"


def test_bridge_image(tmp_path: Path) -> None:
    data = _build_minimal_image()
    path = _write_file(tmp_path, "test.png", data)
    artifact = ingest_artifact(path, "CASE-001", "tester", enforce_readonly=False)
    extracted = extract_from_artifact(artifact)

    assert extracted.plane_container is not None
    assert extracted.plane_container.image_width == 10


def test_bridge_unsupported_type(tmp_path: Path) -> None:
    path = _write_file(tmp_path, "unknown.bin", b"random binary data")
    artifact = ingest_artifact(path, "CASE-001", "tester", enforce_readonly=False)
    extracted = extract_from_artifact(artifact)

    assert extracted.plane_container is None
    assert len(extracted.extraction_errors) == 1
    assert "unsupported container type" in extracted.extraction_errors[0]


def test_bridge_emits_zarc_event(tmp_path: Path) -> None:
    from anchorum.forensic.core.provenance import clear_events, emitted_events

    clear_events()
    path = _write_file(tmp_path, "test.eml", _build_minimal_email())
    artifact = ingest_artifact(path, "CASE-ZARC", "tester", enforce_readonly=False)
    extract_from_artifact(artifact, case_id="CASE-ZARC", operator="tester")

    events = emitted_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "metadata_extraction"
    assert events[0]["payload"]["artifact_id"] == artifact.artifact_id
