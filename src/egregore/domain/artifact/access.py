"""Read-only accessor for agents.

Agents receive only this object for artifact access. It exposes no
mutation methods, so even a compromised agent has zero ability to
modify secured artifacts.
"""

from __future__ import annotations

from .store import ArtifactStore
from .models import SecuredArtifact, ImmutableArtifactError


class AgentArtifactAccessor:
    """Provides read-only access to secured artifacts.

    Intentionally does NOT define update/delete/save methods.
    """

    def __init__(self, store: ArtifactStore):
        self._store = store

    def read(self, artifact_id: str) -> SecuredArtifact:
        """Return the secured artifact metadata (hash, provenance)."""
        return self._store.read(artifact_id)

    def verify_content(self, artifact_id: str, content: str) -> bool:
        """Check if the provided content matches the secured hash."""
        art = self.read(artifact_id)
        import hashlib
        return hashlib.sha256(content.encode("utf-8")).hexdigest() == art.content_hash
