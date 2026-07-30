# epistemic marker: provenance / auditability
"""Shared ports (protocols) used by application and infrastructure layers.

Keeping interface protocols in ``shared`` lets infrastructure implementations
import them without violating the layer matrix.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from egregore.domain.job_models import NodeCapability
from egregore.domain.scheduler_models import Job


@runtime_checkable
class INodeStore(Protocol):
    """Persistence port for node capability records."""

    def upsert(self, node: NodeCapability) -> None: ...

    def get(self, node_id: str) -> NodeCapability | None: ...

    def get_all(self) -> list[NodeCapability]: ...

    def get_by_capability(self, capability: str) -> list[NodeCapability]: ...

    def get_active(self, cutoff_ticks: int) -> list[NodeCapability]: ...

    def deprecate(self, node_id: str) -> bool: ...


@runtime_checkable
class IJobStore(Protocol):
    """Persistence port for scheduler job records."""

    def insert(self, job: Job) -> bool: ...

    def fetch_pending(self, tenant_id: str, limit: int) -> list[Job]: ...

    def update_status(self, job_id: str, status: str, node_id: str = "") -> bool: ...

    def count_by_status(self, tenant_id: str) -> dict[str, int]: ...

    def oldest_pending(self, tenant_id: str) -> int: ...
