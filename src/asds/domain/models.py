"""Minimal ASDS domain entities used by ANCHORUM port contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Evidence:
    """A single piece of evidence registered in an ASDS workspace/case."""

    id: str
    workspace_id: str
    case_id: str | None
    content_ref: str
    content_hash: str
    container_type: str
    mime_type: str | None
    size_bytes: int
    original_filename: str | None
    filesystem_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class OutputVersion:
    """Immutable analysis output produced by an ANCHORUM module."""

    id: str
    evidence_id: str
    analysis_type: str
    version_number: int
    content_hash: str
    content_ref: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
