from __future__ import annotations

from egregore.factory.intake.intake_service import IntakeService
from egregore.factory.schemas.task_envelope import (
    CreateTaskRequest,
    ForensicGate,
    SourceType,
    TaskType,
)


def test_accept_chat_message():
    service = IntakeService(dedup_window=0)
    envelope = service.accept_chat_message({"role": "user", "content": "Write a factorial function"})
    assert envelope.source.source_type == SourceType.CHAT
    assert envelope.task_type == TaskType.CHAT
    assert envelope.payload.text == "Write a factorial function"
    assert any(p["station"] == "intake" for p in envelope.provenance_chain)


def test_accept_email_envelope():
    service = IntakeService(dedup_window=0)
    email = {
        "message_id": "<abc123>",
        "subject": "Evidence submission",
        "body_plain": "Please find attached evidence.",
        "from": "witness@example.com",
        "attachments": [{"filename": "photo.jpg"}],
    }
    envelope = service.accept_email_envelope(email)
    assert envelope.source.source_type == SourceType.EMAIL
    assert envelope.task_type == TaskType.DOCUMENT_INGEST
    assert "photo.jpg" in envelope.payload.text
    assert envelope.payload.subject == "Evidence submission"


def test_deduplication():
    service = IntakeService(dedup_window=100)
    req = CreateTaskRequest(source_type=SourceType.API, text="duplicate")
    e1 = service.accept(req)
    e2 = service.accept(req)
    assert any(p["action"] == "deduplicated" for p in e2.provenance_chain)
    assert e1.fingerprint() == e2.fingerprint()


def test_upload_without_sha256_flagged_for_review():
    service = IntakeService(dedup_window=0)
    envelope = service.accept_document("file.txt", "text/plain", "Zm9v")
    assert envelope.forensic_gate == ForensicGate.REVIEW
    assert "missing-hash" in envelope.tags


def test_anchorum_critical_anomaly_quarantined():
    service = IntakeService(dedup_window=0)
    envelope = service.accept(
        CreateTaskRequest(
            source_type=SourceType.ANCHORUM,
            source_id="art-1",
            filename="suspicious.pdf",
            metadata={"anomalies": [{"severity": "critical", "description": "metadata wiped"}]},
        )
    )
    assert envelope.forensic_gate == ForensicGate.QUARANTINED
    assert "auto-quarantined" in envelope.tags


def test_clean_document_passes_gate():
    service = IntakeService(dedup_window=0)
    envelope = service.accept(
        CreateTaskRequest(
            source_type=SourceType.API,
            text="What is the statute of limitations?",
        )
    )
    assert envelope.forensic_gate == ForensicGate.CLEAN
