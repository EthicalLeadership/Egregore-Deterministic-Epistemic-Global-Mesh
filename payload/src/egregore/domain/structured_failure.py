# epistemic marker: provenance / auditability
"""StructuredFailure - deterministic, auditable failure representation."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class StructuredFailure:
    """
    Every exception caught by ExecutionGuard becomes a StructuredFailure.

    No raw stack traces leak. All failures are serializable, hashable,
    and anchor-ready.
    """

    failure_id: str
    subsystem: str
    operation: str
    message: str
    retryable: bool = False
    severity: str = "medium"

    @staticmethod
    def from_exception(
        e: Exception, subsystem: str, operation: str
    ) -> "StructuredFailure":
        """Factory: convert any Exception into a structured, serializable failure."""
        return StructuredFailure(
            failure_id=str(uuid.uuid4()),
            subsystem=subsystem,
            operation=operation,
            message=str(e),
            retryable=False,
            severity="high",
        )
