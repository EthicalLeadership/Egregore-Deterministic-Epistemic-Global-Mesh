# epistemic marker: provenance / auditability
# Domain-level semantics ports — no outer-layer imports allowed
from typing import Any, Protocol


class ISemanticsDomainAdapter(Protocol):
    """Port for semantics domain adaptation."""

    def adapt(self, raw: Any) -> Any: ...
