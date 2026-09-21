from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from egregore.domain.provenance_model import ProvenanceEvent


class IProvenanceSink(Protocol):
    def append(self, event: ProvenanceEvent) -> None: ...


class IProvenanceVerifier(Protocol):
    def verify_chain(self) -> bool: ...


# Optional helper shape for adapters that want to pass through generic metadata.
ProvenanceEventLike = Mapping[str, Any]
