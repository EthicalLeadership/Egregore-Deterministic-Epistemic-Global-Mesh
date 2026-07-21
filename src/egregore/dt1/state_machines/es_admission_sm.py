from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from egregore.dt1.models import (
    BladeDispatchOutcome,
    Dt1Class,
    LaneKey,
    PressureSignal,
    WorkUnit,
)
from egregore.dt1.state_machines.pressure_aggregation_sm import (
    PressureAggregate,
    PressureDebounceState,
    aggregate_pressure_level,
    apply_pressure_hysteresis,
)


@dataclass(frozen=True)
class EsAdmissionInputs:
    now_unix_nanos: int

    # Pressure level emitted by BPC (0..3).
    pressure_level: int

    # Whether this WorkUnit is treated as latency-critical (bypasses shed rule).
    critical: bool

    # Credits lease for this lane (lease TTL and amounts).
    credits_wu: int
    credits_bytes: int
    credits_ttl_ms_remaining: int
    credits_epoch: int
    need_wu: int
    need_bytes: int

    # If we run out of credits, deterministic defer policy.
    # If deadline allows defer, we return DEFERRED with retry_after_ms.
    min_defer_slack_nanos: int
    retry_after_ms: int

    # Placement decision.
    edge_can_execute: bool


@dataclass(frozen=True)
class EsPressurePolicy:
    scope: str = "edge"
    upshift_ticks_required: int = 1
    downshift_ticks_required: int = 2


def resolve_es_pressure_level(
    *,
    signals: Sequence[PressureSignal],
    site: str,
    previous: PressureDebounceState,
    policy: EsPressurePolicy | None = None,
) -> tuple[int, PressureDebounceState, PressureAggregate]:
    policy = policy if policy is not None else EsPressurePolicy()
    aggregate = aggregate_pressure_level(
        signals=signals,
        site=site,
        scope=policy.scope,
    )
    next_state = apply_pressure_hysteresis(
        previous=previous,
        raw_level=aggregate.raw_level,
        upshift_ticks_required=policy.upshift_ticks_required,
        downshift_ticks_required=policy.downshift_ticks_required,
    )
    return next_state.effective_level, next_state, aggregate


def _credits_usable(
    *,
    ttl_ms_remaining: int,
    need_wu: int,
    need_bytes: int,
    credits_wu: int,
    credits_bytes: int,
) -> bool:
    if ttl_ms_remaining <= 0:
        return False
    if credits_wu < need_wu:
        return False
    return not credits_bytes < need_bytes


def admit_workunit_es(
    *, wu: WorkUnit, lane: LaneKey, inputs: EsAdmissionInputs
) -> BladeDispatchOutcome:
    """
    Deterministic ES admission/publish decision (Spec-3 §2.1, golden path).

    Decision mapping:
    - pressure>=3 && !critical            => REJECTED
    - credits not usable (expired/insufficient) =>
        - deadline allows defer         => DEFERRED
        - else                           => REJECTED
    - credits usable => ADMITTED
        - dt1_class=L && edge_can_execute  => ACCEPTED (local exec)
        - else                              => PUBLISHED (offload path)
    """
    if inputs.pressure_level >= 3 and not inputs.critical:
        return BladeDispatchOutcome(
            decision="REJECTED",
            retry_after_ms=0,
            reason="pressure_critical_and_not_critical_wu",
            published=False,
        )

    credits_ok = _credits_usable(
        ttl_ms_remaining=inputs.credits_ttl_ms_remaining,
        need_wu=inputs.need_wu,
        need_bytes=inputs.need_bytes,
        credits_wu=inputs.credits_wu,
        credits_bytes=inputs.credits_bytes,
    )

    if not credits_ok:
        deadline_slack = int(wu.env.deadline_unix_nanos) - int(inputs.now_unix_nanos)
        if deadline_slack >= inputs.min_defer_slack_nanos:
            return BladeDispatchOutcome(
                decision="DEFERRED",
                retry_after_ms=int(inputs.retry_after_ms),
                reason="insufficient_or_expired_credits_deadline_allows",
                published=False,
            )
        return BladeDispatchOutcome(
            decision="REJECTED",
            retry_after_ms=0,
            reason="insufficient_or_expired_credits_deadline_exceeded",
            published=False,
        )

    # Credits usable -> admitted.
    # Placement:
    # - If dt1_class=L and we can execute at the edge => ACCEPTED (local exec)
    # - else publish/offload.
    if wu.env.dt1_class == Dt1Class.DT1_CLASS_L and inputs.edge_can_execute:
        return BladeDispatchOutcome(
            decision="ACCEPTED",
            retry_after_ms=0,
            reason="admitted_local_edge_execution",
            published=False,
        )

    return BladeDispatchOutcome(
        decision="PUBLISHED",
        retry_after_ms=0,
        reason="admitted_offloaded_to_downstream",
        published=True,
    )
