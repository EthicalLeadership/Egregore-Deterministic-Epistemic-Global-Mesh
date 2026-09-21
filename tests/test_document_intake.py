from __future__ import annotations

from io import BytesIO

import pytest

docx = pytest.importorskip("python-docx")
pdfplumber = pytest.importorskip("pdfplumber")

from egregore.application.document_intake import (
    _classify_document,
    _content_type_from_suffix,
    _sha256_hex,
    build_dossier_request_from_intake,
    extract_document,
    extract_text_from_docx,
)
from egregore.shared.canonical import canonical_dumps


def _make_docx_bytes(text: str) -> bytes:
    """Create a minimal DOCX in memory."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_text_from_docx():
    text = "Standard Operating Procedure for cannabis cultivation."
    b = _make_docx_bytes(text)
    result = extract_text_from_docx(b)
    assert text in result


def test_classify_document_sop():
    result = _classify_document(
        "SOP_Cultivation_v2.docx", "Standard Operating Procedure"
    )
    assert result["doc_type"] == "sop"
    assert result["jurisdiction"] == "ca-federal"


def test_classify_document_license():
    result = _classify_document(
        "Health_Canada_License.pdf", "license granted by Health Canada"
    )
    assert result["doc_type"] == "license"
    assert result["jurisdiction"] == "ca-health-canada"


def test_classify_document_seed_to_sale():
    result = _classify_document(
        "Batch_001_Manifest.pdf", "seed-to-sale manifest for lot 42"
    )
    assert result["doc_type"] == "seed_to_sale_manifest"


def test_classify_document_risk_flags():
    result = _classify_document(
        "old_license.pdf", "this license is expired and non-compliant"
    )
    assert "expired_document" in result["risk_flags"]
    assert "compliance_violation" in result["risk_flags"]


def test_content_type_from_suffix():
    assert _content_type_from_suffix(".pdf") == "application/pdf"
    assert _content_type_from_suffix(".docx") == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert _content_type_from_suffix(".txt") == "text/plain"
    assert _content_type_from_suffix(".unknown") == "application/octet-stream"


def test_extract_document_txt_fallback():
    text = "Hello, this is a plain text file."
    b = text.encode("utf-8")
    doc = extract_document(b, "readme.txt")
    assert doc.filename == "readme.txt"
    assert doc.text == text
    assert doc.content_type == "text/plain"
    assert doc.fingerprint == _sha256_hex(b)


def test_build_dossier_request_determinism():
    """Same inputs must produce identical fingerprints."""
    docs = [
        extract_document(b"content-a", "a.txt"),
        extract_document(b"content-b", "b.txt"),
    ]
    req1 = build_dossier_request_from_intake(
        organization_id="org-1",
        case_id="case-1",
        actor_id="actor-1",
        causality_id="cause-1",
        vertical="cannabis",
        documents=docs,
    )
    req2 = build_dossier_request_from_intake(
        organization_id="org-1",
        case_id="case-1",
        actor_id="actor-1",
        causality_id="cause-1",
        vertical="cannabis",
        documents=docs,
    )
    assert req1.input_fingerprint == req2.input_fingerprint


def test_build_dossier_request_vertical_none():
    docs = [extract_document(b"x", "x.txt")]
    req = build_dossier_request_from_intake(
        organization_id="org-1",
        case_id="case-1",
        actor_id="actor-1",
        causality_id="cause-1",
        vertical=None,
        documents=docs,
    )
    assert req.vertical is None
    assert req.engine_version == "intake_v1"
    assert req.policy_version == "cannabis_policy_v1"


def test_build_dossier_request_payload_shape():
    docs = [extract_document(b"y", "y.txt")]
    req = build_dossier_request_from_intake(
        organization_id="org-1",
        case_id="case-1",
        actor_id="actor-1",
        causality_id="cause-1",
        vertical="cannabis",
        documents=docs,
    )
    payload = req.input_payload
    assert payload["intake_type"] == "document_upload"
    assert payload["vertical"] == "cannabis"
    assert len(payload["documents"]) == 1
    assert "classification" in payload["documents"][0]
    assert "extracted_text" in payload["documents"][0]


def test_fingerprint_matches_canonical_payload():
    docs = [extract_document(b"z", "z.txt")]
    req = build_dossier_request_from_intake(
        organization_id="org-1",
        case_id="case-1",
        actor_id="actor-1",
        causality_id="cause-1",
        vertical="cannabis",
        documents=docs,
    )
    expected = _sha256_hex(canonical_dumps(req.input_payload).encode("utf-8"))
    assert req.input_fingerprint == expected
