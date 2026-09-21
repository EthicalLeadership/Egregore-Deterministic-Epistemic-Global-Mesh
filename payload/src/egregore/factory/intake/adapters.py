"""Adapters that convert external inputs into TaskEnvelope objects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from egregore.factory.schemas.task_envelope import (
    CreateTaskRequest,
    ForensicGate,
    SourceType,
    TaskEnvelope,
    TaskPayload,
    TaskProvenance,
    TaskType,
)


def _now() -> datetime:
    return datetime.now(UTC)


def from_create_request(request: CreateTaskRequest, remote_addr: str | None = None) -> TaskEnvelope:
    """Convert a generic CreateTaskRequest into a TaskEnvelope.

    This is the default adapter used by the HTTP intake endpoint. It preserves
    all provided metadata and applies a heuristic task-type classification if
    none was supplied.
    """
    provenance = TaskProvenance(
        source_type=request.source_type,
        source_id=request.source_id,
        received_at=_now(),
        remote_addr=remote_addr,
        operator_id=request.operator_id,
        correlation_id=request.correlation_id,
    )
    payload = TaskPayload(
        text=request.text,
        subject=request.subject,
        filename=request.filename,
        content_type=request.content_type,
        bytes_b64=request.bytes_b64,
        sha256=request.sha256,
        metadata=request.metadata,
    )
    task_type = request.task_type or _classify_payload(payload, request.source_type)
    return TaskEnvelope(
        task_type=task_type,
        source=provenance,
        payload=payload,
        context_budget=request.context_budget,
        priority=request.priority,
        tags=request.tags,
    )


def from_chat_message(
    message: dict[str, Any],
    operator_id: str | None = None,
    remote_addr: str | None = None,
) -> TaskEnvelope:
    """Normalize an OpenAI-style chat message into a TaskEnvelope."""
    content = message.get("content") or ""
    if isinstance(content, list):
        # Multimodal content: join text parts, ignore images for now.
        content = " ".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict) and "text" in part
        )
    return from_create_request(
        CreateTaskRequest(
            source_type=SourceType.CHAT,
            text=str(content),
            task_type=TaskType.CHAT,
            operator_id=operator_id,
        ),
        remote_addr=remote_addr,
    )


def from_email_envelope(
    envelope: dict[str, Any],
    operator_id: str | None = None,
    remote_addr: str | None = None,
) -> TaskEnvelope:
    """Normalize an email envelope (as produced by GDC intake.py) into a TaskEnvelope."""
    text_parts: list[str] = []
    body = envelope.get("body_plain") or envelope.get("body_html") or ""
    if body:
        text_parts.append(str(body))
    for attachment in envelope.get("attachments") or []:
        text_parts.append(f"[ATTACHMENT: {attachment.get('filename', 'unknown')}]")

    metadata = {
        "from": envelope.get("from"),
        "to": envelope.get("to"),
        "cc": envelope.get("cc"),
        "message_id": envelope.get("message_id"),
        "date": envelope.get("date"),
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}

    return from_create_request(
        CreateTaskRequest(
            source_type=SourceType.EMAIL,
            source_id=envelope.get("message_id"),
            subject=envelope.get("subject"),
            text="\n\n".join(text_parts),
            metadata=metadata,
            task_type=TaskType.DOCUMENT_INGEST,
            operator_id=operator_id,
        ),
        remote_addr=remote_addr,
    )


def from_document_upload(
    filename: str,
    content_type: str,
    bytes_b64: str,
    sha256: str | None = None,
    text_preview: str | None = None,
    operator_id: str | None = None,
    remote_addr: str | None = None,
) -> TaskEnvelope:
    """Normalize a document upload into a TaskEnvelope."""
    return from_create_request(
        CreateTaskRequest(
            source_type=SourceType.UPLOAD,
            filename=filename,
            content_type=content_type,
            bytes_b64=bytes_b64,
            sha256=sha256,
            text=text_preview,
            task_type=TaskType.DOCUMENT_INGEST,
            operator_id=operator_id,
        ),
        remote_addr=remote_addr,
    )


def from_anchorum_artifact(
    artifact: dict[str, Any],
    operator_id: str | None = None,
    remote_addr: str | None = None,
) -> TaskEnvelope:
    """Normalize an ANCHORUM artifact record into a TaskEnvelope."""
    envelope = from_create_request(
        CreateTaskRequest(
            source_type=SourceType.ANCHORUM,
            source_id=artifact.get("artifact_id") or artifact.get("sha256"),
            filename=artifact.get("filename"),
            sha256=artifact.get("sha256"),
            text=artifact.get("text_preview") or artifact.get("description"),
            metadata={k: v for k, v in artifact.items() if k not in ("artifact_id", "sha256", "filename", "text_preview")},
            task_type=TaskType.FORENSIC_QUERY,
            operator_id=operator_id,
        ),
        remote_addr=remote_addr,
    )
    # Pre-apply gate status if the artifact already carries one.
    gate = artifact.get("gate_status")
    if gate == "quarantined":
        envelope = envelope.model_copy(update={"forensic_gate": ForensicGate.QUARANTINED})
    elif gate == "clean":
        envelope = envelope.model_copy(update={"forensic_gate": ForensicGate.CLEAN})
    return envelope


def _classify_payload(payload: TaskPayload, source_type: SourceType) -> TaskType:
    """Heuristic task-type classifier for raw payloads."""
    if source_type == SourceType.ANCHORUM:
        return TaskType.FORENSIC_QUERY
    if payload.bytes_b64 or payload.filename:
        return TaskType.DOCUMENT_INGEST
    if payload.text:
        text_lower = payload.text.lower()
        if any(word in text_lower for word in ("correlate", "connection", "link between")):
            return TaskType.CORRELATE
        if any(word in text_lower for word in ("review", "critique", "check this")):
            return TaskType.CRITICAL_REVIEW
        if any(word in text_lower for word in ("forensic", "anomaly", "hash", "metadata")):
            return TaskType.FORENSIC_QUERY
        return TaskType.CHAT
    return TaskType.UNKNOWN
