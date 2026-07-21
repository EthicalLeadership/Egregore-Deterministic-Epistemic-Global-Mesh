"""Job domain models - immutable, hashable, serializable.

All models are frozen dataclasses. They represent the contract between
Atmosphere (ingress), Crust (agencies), and Mantle (regulators).
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    """Deterministic job lifecycle states."""

    PENDING = "PENDING"
    CLASSIFIED = "CLASSIFIED"
    SCHEDULED = "SCHEDULED"
    ROUTED = "ROUTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FROZEN = "FROZEN"


class ComplexityTier(StrEnum):
    """Job complexity classification."""

    TRIVIAL = "TRIVIAL"
    STANDARD = "STANDARD"
    COMPLEX = "COMPLEX"
    SOVEREIGN = "SOVEREIGN"


@dataclass(frozen=True)
class ResourceProfile:
    cpu_percent: float = 0.0
    memory_mb: int = 0
    vram_mb: int = 0
    disk_iops: int = 0
    network_mbps: int = 0

    def can_satisfy(self, need: "ResourceProfile") -> bool:
        return (
            self.cpu_percent >= need.cpu_percent
            and self.memory_mb >= need.memory_mb
            and self.vram_mb >= need.vram_mb
            and self.disk_iops >= need.disk_iops
            and self.network_mbps >= need.network_mbps
        )


@dataclass(frozen=True)
class JobRequest:
    job_id: str
    tenant_id: str
    trace_id: str
    payload: dict[str, Any]
    requested_capabilities: list[str] = field(default_factory=list)
    priority_hint: str = "STANDARD"
    sla_deadline_ns: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JobClassification:
    job_id: str
    complexity: "ComplexityTier"
    resource_profile: "ResourceProfile"
    estimated_tokens: int = 0
    target_vertical: str = ""
    requested_capabilities: list[str] = field(default_factory=list)
    deterministic_required: bool = False
    priority_tier: str = ""
    created_at_ns: int = 0


@dataclass(frozen=True)
class NodeHeartbeat:
    node_id: str
    timestamp_ns: int
    load_metrics: ResourceProfile
    available_capabilities: list[str] = field(default_factory=list)
    active_job_count: int = 0
    uptime_ticks: int = 0
    public_key_fingerprint: str | None = None


@dataclass(frozen=True)
class NodeCapability:
    node_id: str
    capabilities: list[str] = field(default_factory=list)
    resource_profile: ResourceProfile = field(default_factory=ResourceProfile)
    trust_score: float = 0.5
    last_heartbeat_ns: int = 0
    status: str = "UNKNOWN"
    public_key_fingerprint: str | None = None


@dataclass(frozen=True)
class SealedEvidence:
    evidence_id: str
    job_id: str
    node_id: str
    tenant_id: str
    timestamp_ns: int
    result_hash: str
    input_hash: str
    duration_ms: int
    success: bool
    signature: str = ""


@dataclass(frozen=True)
class RoutingDecision:
    job_id: str
    node_id: str
    tenant_id: str
    trace_id: str
    decision_reason: str
    estimated_latency_ms: int = 0
    fallback_chain: list[str] = field(default_factory=list)
