"""Immutability domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from datetime import datetime, timezone
import hashlib


class ArtifactStatus(str, Enum):
    SECURED = "secured"
    DRAFT = "draft"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class AuthorizationToken:
    """Human-signed permission for a specific mutation."""
    artifact_id: str
    operation: str
    token: str
    expires_at: datetime
    granted_by: str

    def is_valid(self) -> bool:
        return datetime.now(timezone.utc) < self.expires_at


@dataclass(frozen=True)
class SecuredArtifact:
    """Immutable artifact secured in Anchorum/Legal Dossier."""
    id: str
    content_hash: str
    status: ArtifactStatus = ArtifactStatus.SECURED
    provenance: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_content(cls, artifact_id: str, content: str) -> "SecuredArtifact":
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return cls(id=artifact_id, content_hash=h)


class ImmutableArtifactError(Exception):
    """Raised when mutation is attempted on a secured artifact."""
    pass
