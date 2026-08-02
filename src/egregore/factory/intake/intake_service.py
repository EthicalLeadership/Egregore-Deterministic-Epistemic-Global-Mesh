"""Intake service — turns raw inputs into canonical TaskEnvelope objects.

The service is intentionally lightweight: it normalizes, deduplicates, applies
a forensic gate decision, and records provenance. Heavy processing (email
parsing, document text extraction, ANCHORUM forensics) is delegated to adapters
and external pipelines.
"""

from __future__ import annotations

import logging
from typing import Any

from egregore.factory.intake.adapters import from_create_request
from egregore.factory.schemas.task_envelope import (
    CreateTaskRequest,
    ForensicGate,
    SourceType,
    TaskEnvelope,
)

logger = logging.getLogger(__name__)


class IntakeService:
    """Factory intake boundary.

    Args:
        dedup_window: Maximum number of recent fingerprints to retain for
            deduplication. Set to 0 to disable.
        quarantine_on_anchorum_critical: If True, any ANCHORUM artifact with a
            critical severity anomaly is immediately quarantined.

    """

    def __init__(self, dedup_window: int = 10_000, quarantine_on_anchorum_critical: bool = True) -> None:
        self.dedup_window = dedup_window
        self.quarantine_on_anchorum_critical = quarantine_on_anchorum_critical
        self._recent_fingerprints: set[str] = set()
        self._recent_order: list[str] = []

    def accept(
        self,
        request: CreateTaskRequest,
        remote_addr: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> TaskEnvelope:
        """Normalize a request into a TaskEnvelope and apply intake gates."""
        envelope = from_create_request(request, remote_addr=remote_addr)
        if extra_metadata:
            envelope.payload.metadata.update(extra_metadata)

        envelope = envelope.add_provenance("intake", "normalized", {"source": envelope.source.source_type.value})
        return self._post_process(envelope)

    def _post_process(self, envelope: TaskEnvelope) -> TaskEnvelope:
        """Apply deduplication and forensic gate to an already-normalized envelope."""
        if self._is_duplicate(envelope):
            envelope = envelope.add_provenance("intake", "deduplicated", {"fingerprint": envelope.fingerprint()})
        return self._apply_forensic_gate(envelope)

    def accept_chat_message(
        self,
        message: dict[str, Any],
        operator_id: str | None = None,
        remote_addr: str | None = None,
    ) -> TaskEnvelope:
        """Convenience helper for OpenAI-style chat messages."""
        from egregore.factory.intake.adapters import from_chat_message

        envelope = from_chat_message(message, operator_id=operator_id, remote_addr=remote_addr)
        return self._post_process(envelope)

    def accept_email_envelope(
        self,
        envelope: dict[str, Any],
        operator_id: str | None = None,
        remote_addr: str | None = None,
    ) -> TaskEnvelope:
        """Convenience helper for GDC-style email envelopes."""
        from egregore.factory.intake.adapters import from_email_envelope

        task = from_email_envelope(envelope, operator_id=operator_id, remote_addr=remote_addr)
        return self._post_process(task)

    def accept_document(
        self,
        filename: str,
        content_type: str,
        bytes_b64: str,
        sha256: str | None = None,
        text_preview: str | None = None,
        operator_id: str | None = None,
        remote_addr: str | None = None,
    ) -> TaskEnvelope:
        """Convenience helper for document uploads."""
        from egregore.factory.intake.adapters import from_document_upload

        task = from_document_upload(
            filename=filename,
            content_type=content_type,
            bytes_b64=bytes_b64,
            sha256=sha256,
            text_preview=text_preview,
            operator_id=operator_id,
            remote_addr=remote_addr,
        )
        return self._post_process(task)

    def _is_duplicate(self, envelope: TaskEnvelope) -> bool:
        """Check whether an equivalent task was recently seen."""
        if self.dedup_window <= 0:
            return False
        fingerprint = envelope.fingerprint()
        if fingerprint in self._recent_fingerprints:
            return True
        self._recent_fingerprints.add(fingerprint)
        self._recent_order.append(fingerprint)
        while len(self._recent_order) > self.dedup_window:
            oldest = self._recent_order.pop(0)
            self._recent_fingerprints.discard(oldest)
        return False

    def _apply_forensic_gate(self, envelope: TaskEnvelope) -> TaskEnvelope:
        """Apply deterministic intake gate rules.

        This is intentionally simple: a full ANCHORUM forensic scan happens
        asynchronously later. At intake we only enforce hard rules based on
        already-known metadata.
        """
        # ANCHORUM artifacts with explicit critical anomalies are quarantined.
        if envelope.source.source_type == SourceType.ANCHORUM and self.quarantine_on_anchorum_critical:
            anomalies = envelope.payload.metadata.get("anomalies") or []
            severities = {a.get("severity", "").lower() for a in anomalies if isinstance(a, dict)}
            if "critical" in severities:
                return envelope.model_copy(
                    update={
                        "forensic_gate": ForensicGate.QUARANTINED,
                        "tags": [*envelope.tags, "auto-quarantined"],
                    }
                ).add_provenance("intake", "forensic_gate", {"status": "quarantined", "reason": "critical anomaly"})

        # Uploaded documents without a SHA-256 are flagged for review.
        if envelope.source.source_type == SourceType.UPLOAD and not envelope.payload.sha256:
            return envelope.model_copy(
                update={
                    "forensic_gate": ForensicGate.REVIEW,
                    "tags": [*envelope.tags, "missing-hash"],
                }
            ).add_provenance("intake", "forensic_gate", {"status": "review", "reason": "missing sha256"})

        return envelope.model_copy(update={"forensic_gate": ForensicGate.CLEAN}).add_provenance(
            "intake", "forensic_gate", {"status": "clean"}
        )
