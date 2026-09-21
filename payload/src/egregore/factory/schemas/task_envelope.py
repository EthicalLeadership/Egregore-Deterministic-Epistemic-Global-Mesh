"""Task envelope schema — the single boundary object for the AI factory.

Every input to the factory (chat message, email, uploaded file, ANCHORUM artifact,
API call) is normalized into a TaskEnvelope. Downstream stations consume this
schema instead of raw input formats.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class TaskType(StrEnum):
    """High-level task types the factory knows how to route."""

    LEGAL_QUERY = "legal_query"
    DOCUMENT_INGEST = "document_ingest"
    FORENSIC_QUERY = "forensic_query"
    CORRELATE = "correlate"
    CHAT = "chat"
    CRITICAL_REVIEW = "critical_review"
    UNKNOWN = "unknown"


class Priority(StrEnum):
    """Processing priority."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class SourceType(StrEnum):
    """Where the task came from."""

    CHAT = "chat"
    EMAIL = "email"
    UPLOAD = "upload"
    API = "api"
    ANCHORUM = "anchorum"


class ForensicGate(StrEnum):
    """Result of the ANCHORUM forensic gate."""

    PENDING = "pending"
    CLEAN = "clean"
    QUARANTINED = "quarantined"
    REVIEW = "review"


class TaskProvenance(BaseModel):
    """Audit trail for a task's origin."""

    source_type: SourceType
    source_id: str | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    remote_addr: str | None = None
    operator_id: str | None = None
    correlation_id: str | None = None


class TaskPayload(BaseModel):
    """Normalized payload extracted from the original input.

    Only one of the typed fields is expected to be populated, depending on
    source_type. Extra fields are allowed for source-specific metadata.
    """

    text: str | None = None
    subject: str | None = None
    filename: str | None = None
    content_type: str | None = None
    bytes_b64: str | None = None
    sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskEnvelope(BaseModel):
    """Canonical factory task.

    Attributes:
        task_id: Stable UUID for this task.
        task_type: Classification used by the router. May be refined later.
        source: Provenance metadata.
        payload: Normalized content extracted from the input.
        context_budget: Max tokens available for context (0 = unset).
        required_capabilities: Capabilities the chosen station must have.
        priority: Processing priority.
        retention_until: Optional legal-hold / retention deadline.
        forensic_gate: ANCHORUM gate status.
        tags: Free-form tags for routing and audit.
        provenance_chain: Ordered list of station actions taken on this task.

    """

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_type: TaskType = TaskType.UNKNOWN
    source: TaskProvenance
    payload: TaskPayload
    context_budget: int = 0
    required_capabilities: list[str] = Field(default_factory=list)
    priority: Priority = Priority.NORMAL
    retention_until: datetime | None = None
    forensic_gate: ForensicGate = ForensicGate.PENDING
    tags: list[str] = Field(default_factory=list)
    provenance_chain: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("context_budget")
    @classmethod
    def _non_negative_budget(cls, value: int) -> int:
        if value < 0:
            raise ValueError("context_budget must be non-negative")
        return value

    def fingerprint(self) -> str:
        """Return a stable SHA-256 fingerprint of the normalized payload.

        Used for deduplication and provenance. The fingerprint ignores
        auto-generated IDs and timestamps.
        """
        canonical = f"{self.source.source_type.value}|{self.payload.text or ''}|{self.payload.sha256 or ''}|{self.payload.filename or ''}|{self.payload.subject or ''}"
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def add_provenance(self, station: str, action: str, detail: dict[str, Any] | None = None) -> TaskEnvelope:
        """Return a new envelope with an added provenance entry."""
        entry = {
            "station": station,
            "action": action,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if detail:
            entry["detail"] = detail
        updated = self.model_copy(deep=True)
        updated.provenance_chain.append(entry)
        return updated


class CreateTaskRequest(BaseModel):
    """External API request to create a task from raw input."""

    source_type: SourceType
    source_id: str | None = None
    text: str | None = None
    subject: str | None = None
    filename: str | None = None
    content_type: str | None = None
    bytes_b64: str | None = None
    sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    task_type: TaskType | None = None
    priority: Priority = Priority.NORMAL
    context_budget: int = 0
    tags: list[str] = Field(default_factory=list)
    operator_id: str | None = None
    correlation_id: str | None = None
