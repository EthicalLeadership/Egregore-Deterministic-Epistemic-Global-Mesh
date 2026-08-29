"""Append-only artifact store with immutability enforcement."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional
import hashlib

from egregore.shared.canonical import canonical_dumps, canonical_loads
from .models import SecuredArtifact, AuthorizationToken, ImmutableArtifactError, ArtifactStatus


class ArtifactStore:
    """Stores secured artifacts and enforces immutability."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts: Dict[str, SecuredArtifact] = {}
        self.journal = root / "artifact_journal.jsonl"

    def load(self) -> None:
        """Load artifacts from disk."""
        if not self.journal.exists():
            return
        with open(self.journal, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = canonical_loads(line)
                    art = SecuredArtifact(
                        id=entry["id"],
                        content_hash=entry["content_hash"],
                        status=ArtifactStatus(entry["status"]),
                        provenance=entry.get("provenance", {}),
                    )
                    self.artifacts[art.id] = art

    def save(self, artifact: SecuredArtifact) -> None:
        """Append artifact to journal (append-only)."""
        entry = {
            "id": artifact.id,
            "content_hash": artifact.content_hash,
            "status": artifact.status.value,
            "provenance": artifact.provenance,
        }
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(canonical_dumps(entry) + "\n")
        self.artifacts[artifact.id] = artifact

    def read(self, artifact_id: str) -> SecuredArtifact:
        """Return a secured artifact (read-only)."""
        if artifact_id not in self.artifacts:
            raise KeyError(f"Artifact {artifact_id} not found")
        return self.artifacts[artifact_id]

    def update(self, artifact_id: str, new_content: str, token: Optional[AuthorizationToken] = None) -> None:
        """Attempt to update a secured artifact. Requires valid token."""
        if artifact_id not in self.artifacts:
            raise KeyError(f"Artifact {artifact_id} not found")
        if not token or not token.is_valid() or token.artifact_id != artifact_id or token.operation != "update":
            raise ImmutableArtifactError(f"Cannot update secured artifact {artifact_id} without valid authorization")
        new_hash = hashlib.sha256(new_content.encode("utf-8")).hexdigest()
        self.artifacts[artifact_id] = SecuredArtifact(
            id=artifact_id,
            content_hash=new_hash,
            status=ArtifactStatus.SECURED,
            provenance={**self.artifacts[artifact_id].provenance, "updated_by": token.granted_by},
        )
        self.save(self.artifacts[artifact_id])

    def delete(self, artifact_id: str, token: Optional[AuthorizationToken] = None) -> None:
        """Attempt to delete a secured artifact. Requires valid token."""
        if artifact_id not in self.artifacts:
            raise KeyError(f"Artifact {artifact_id} not found")
        if not token or not token.is_valid() or token.artifact_id != artifact_id or token.operation != "delete":
            raise ImmutableArtifactError(f"Cannot delete secured artifact {artifact_id} without valid authorization")
        del self.artifacts[artifact_id]
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(canonical_dumps({"id": artifact_id, "deleted": True, "by": token.granted_by}) + "\n")
