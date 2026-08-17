"""Ports and domain errors for mission orchestration.

Kept separate from mission_orchestrator.py so adapters and tests can import
them without circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Protocol

from egregore.domain.job_models import JobClassification, JobRequest


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MissionExecutionError(Exception):
    """Base class for mission orchestration failures."""


class ClassificationError(MissionExecutionError):
    """Raised when root classification fails."""


class WorkTreeError(MissionExecutionError):
    """Raised when work tree decomposition fails."""


class CapacityDeniedError(MissionExecutionError):
    """Raised when capacity admission is denied."""


class RoutingError(MissionExecutionError):
    """Raised when leaf routing fails."""


class NoNodeAvailable(RoutingError):
    """Raised when no capable node is available for a leaf."""


# ---------------------------------------------------------------------------
# Capacity lease
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapacityLease:
    lease_id: str
    root_job_id: str
    total_units: int
    expires_at_ns: int
    status: str = "ACTIVE"


# ---------------------------------------------------------------------------
# Ports (accept sync or async implementations)
# ---------------------------------------------------------------------------


class IJobClassifier(Protocol):
    """Maps a root job request to a deterministic complexity classification."""

    def classify(
        self, request: JobRequest
    ) -> JobClassification | Awaitable[JobClassification]: ...


class IWorkTreeDecomposer(Protocol):
    """Decomposes a classification into leaf job requests."""

    def submit_tree(
        self, classification: JobClassification
    ) -> list[JobRequest] | Awaitable[list[JobRequest]]: ...


class ICapacityOrchestrator(Protocol):
    """Lease-based capacity admission for a mission."""

    def admit(
        self, root_job_id: str, estimated_units: int
    ) -> CapacityLease | None | Awaitable[CapacityLease | None]: ...

    def release(self, lease_id: str) -> None | Awaitable[None]: ...

    def extend(
        self, lease_id: str, extra_units: int
    ) -> CapacityLease | Awaitable[CapacityLease]: ...


class IJobRouter(Protocol):
    """Routes a single leaf job request to a node id."""

    def route_with_fallback(self, request: JobRequest) -> str | Awaitable[str]: ...


class IEventPublisher(Protocol):
    """Structured mission event sink."""

    def publish(self, event_type: str, payload: dict[str, Any]) -> None: ...
