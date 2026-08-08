# epistemic marker: provenance / auditability
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

# -----------------------------
# Enumerations — protobuf-aligned
# -----------------------------


class Dt1Class(IntEnum):
    DT1_CLASS_UNSPECIFIED = 0
    DT1_CLASS_L = 1
    DT1_CLASS_H = 2


class Priority(IntEnum):
    P0 = 0
    P1 = 1
    P2 = 2
    P3 = 3
    P4 = 4
    P5 = 5
    P6 = 6
    P7 = 7


class RoutingHint(IntEnum):
    ROUTING_UNSPECIFIED = 0
    ROUTING_EDGE_ONLY = 1
    ROUTING_CORE_OK = 2
    ROUTING_GPU_REQUIRED = 3


class WorkUnitStatus(IntEnum):
    WU_STATUS_UNSPECIFIED = 0
    WU_OK = 1
    WU_RETRYABLE_ERROR = 2
    WU_FATAL_ERROR = 3
    WU_REJECTED = 4
    WU_DEFERRED = 5
    WU_TIMEOUT = 6


class PressureReason(IntEnum):
    PRESSURE_UNSPECIFIED = 0
    PRESSURE_QUEUE = 1
    PRESSURE_CPU = 2
    PRESSURE_GPU = 3
    PRESSURE_MEMORY = 4
    PRESSURE_ENERGY = 5
    PRESSURE_DEPENDENCY = 6


class WorkSpanKind(IntEnum):
    KIND_UNSPECIFIED = 0
    HEADER = 1
    FEATURES = 2
    TENSOR = 3
    BLOB = 4
    KV_HINTS = 5


# -----------------------------
# Data models
# -----------------------------


@dataclass(frozen=True)
class TraceContext:
    """
    Deterministic trace identity carried end-to-end.

    The u128 parts are modeled as hi/lo fixed64 equivalents to avoid losing entropy.
    """

    trace_id_hi: int
    trace_id_lo: int
    span_id: int
    sampled: bool


@dataclass(frozen=True)
class SpanRef:
    kind: WorkSpanKind
    index: int
    length_bytes: int

    # For small spans: payload embedded inline.
    inline_bytes: bytes = b""

    # For zero-copy intra-host movement: out-of-band shm region metadata.
    shm_region_id: int = 0
    shm_offset: int = 0


@dataclass(frozen=True)
class WorkUnitEnvelope:
    wu_id_hi: int
    wu_id_lo: int
    tenant_id: int

    dt1_class: Dt1Class
    dt1_type: str

    priority: Priority
    deadline_unix_nanos: int

    est_cost_bucket: int
    routing: RoutingHint

    trace: TraceContext
    flags: int = 0
    attempt: int = 0


@dataclass(frozen=True)
class WorkUnit:
    env: WorkUnitEnvelope
    spans: tuple[SpanRef, ...]


@dataclass(frozen=True)
class CostBreakdown:
    h2d_nanos: int
    kernel_nanos: int
    d2h_nanos: int
    cpu_nanos: int


@dataclass(frozen=True)
class WorkUnitResult:
    wu_id_hi: int
    wu_id_lo: int
    status: WorkUnitStatus

    error_code: str = ""
    error_message: str = ""

    service_time_nanos: int = 0
    cost: CostBreakdown | None = None

    # Small results can be inlined.
    output_inline: bytes = b""

    # Large results may be provided via shm offsets.
    output_shm_region_id: int = 0
    output_shm_offset: int = 0
    output_length_bytes: int = 0

    cacheable: bool = False
    est_vram_bytes: int = 0

    def wu_id_tuple(self) -> tuple[int, int]:
        return (self.wu_id_hi, self.wu_id_lo)


@dataclass(frozen=True)
class CreditGrant:
    stage_id: str
    site: str
    dt1_type: str
    priority: Priority

    credits_wu: int
    credits_bytes: int
    ttl_ms: int

    # Credit lease epoch.
    epoch: int


@dataclass(frozen=True)
class CreditRevoke:
    stage_id: str
    site: str
    dt1_type: str
    priority: Priority
    epoch: int


@dataclass(frozen=True)
class PressureSignal:
    stage_id: str
    site: str
    reason: PressureReason
    level: int  # 0..3
    queue_depth_wu: int
    queue_depth_bytes: int
    util: float
    mem_pressure: float
    energy_pressure: float
    ts_unix_nanos: int


@dataclass(frozen=True)
class AdmissionDecision:
    ingress_id: str
    site: str
    dt1_type: str
    priority: Priority

    admit_rate_wu_s: float
    shed_rate_wu_s: float
    reason: str
    ts_unix_nanos: int


# -----------------------------
# Helper aggregates used in deterministic logic
# -----------------------------


@dataclass(frozen=True)
class LaneKey:
    """
    Lane identity for ES/GBS/BPC credit and scheduling decisions.

    This mirrors the subject taxonomy ingredients:
    (class, dt1_type, priority, site).
    """

    dt1_class: Dt1Class
    dt1_type: str
    priority: Priority
    site: str


@dataclass(frozen=True)
class CreditLedger:
    """
    Mutable behavior is intentionally not modeled here.
    This is a pure snapshot used by deterministic state transitions.
    """

    credits_wu: int
    credits_bytes: int
    ttl_ms_remaining: int
    epoch: int

    def has_credits(self, *, need_wu: int = 1, need_bytes: int = 0) -> bool:
        if need_wu > 0 and self.credits_wu < need_wu:
            return False
        if need_bytes > 0 and self.credits_bytes < need_bytes:
            return False
        return self.ttl_ms_remaining > 0


@dataclass(frozen=True)
class BladeDispatchOutcome:
    """
    Result of an ES/BPC/GBS decision about what to do with a WorkUnit.
    """

    # terminal-ish decision
    decision: str  # ACCEPTED | DEFERRED | REJECTED | DLQ | ADMITTED
    retry_after_ms: int = 0
    reason: str = ""

    # routing side effects
    published: bool = False
