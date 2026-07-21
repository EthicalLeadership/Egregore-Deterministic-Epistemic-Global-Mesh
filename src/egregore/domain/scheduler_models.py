"""Scheduler domain models - immutable, deterministic ordering primitives."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PriorityTier(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SLAClass(StrEnum):
    REALTIME = "REALTIME"
    INTERACTIVE = "INTERACTIVE"
    STANDARD = "STANDARD"
    BATCH = "BATCH"
    BEST_EFFORT = "BEST_EFFORT"


@dataclass(frozen=True)
class SLA:
    latency_target_ms: int = 1000
    throughput_target_qps: float = 1.0
    reliability_target: float = 0.99
    class_: SLAClass = SLAClass.STANDARD

    def is_breached(self, actual_latency_ms: int, actual_success: bool) -> bool:
        if not actual_success:
            return True
        return actual_latency_ms > self.latency_target_ms


@dataclass(frozen=True)
class Job:
    job_id: str
    tenant_id: str
    trace_id: str
    priority_tier: PriorityTier
    sla: SLA
    classification: Any
    status: str = "PENDING"
    created_at_ns: int = 0
    scheduled_at_ns: int = 0
    started_at_ns: int = 0
    completed_at_ns: int = 0
    assigned_node_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def priority_ordinal(self) -> int:
        mapping = {
            PriorityTier.CRITICAL: 0,
            PriorityTier.HIGH: 1,
            PriorityTier.MEDIUM: 2,
            PriorityTier.LOW: 3,
        }
        return mapping.get(self.priority_tier, 99)

    @property
    def urgency_score(self) -> float:
        base = self.priority_ordinal * 1000.0
        if self.sla.latency_target_ms <= 0:
            return base
        return base + (1.0 / max(self.sla.latency_target_ms, 1))


@dataclass(frozen=True)
class QueueSnapshot:
    tick: int
    tenant_id: str
    depth_by_priority: dict[str, int]
    oldest_pending_ns: int
    total_wait_ms: int
    snapshot_hash: str = ""
