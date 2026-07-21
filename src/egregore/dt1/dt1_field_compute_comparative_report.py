from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from egregore.dt1.panelmesh_phase1_runtime import PanelExecutionRecord, PanelState


@dataclass(frozen=True)
class ComparativeDelta:
    absolute_change: float | None
    percentage_change: float | None
    stabilization_time_ticks: int


@dataclass(frozen=True)
class ReportPhase:
    # Required comparative section buckets
    core_compute_metrics: dict[str, float | None]
    stability_metrics: dict[str, float | None]
    entropy_metrics: dict[str, float | None]
    reservoir_field_metrics: dict[str, float | None]
    power_thermal_metrics: dict[str, float | None]

    # Optional introspection strings (kept deterministic from inputs)
    failure_propagation_map: dict[str, Any]
    bottleneck_migration_analysis: dict[str, Any]

    # Interpretation layer strings for audit narrative
    interpretation: dict[str, str]


def _count_panels_by_state(
    panels: Mapping[str, PanelExecutionRecord],
) -> dict[PanelState, int]:
    counts: dict[PanelState, int] = dict.fromkeys(PanelState, 0)
    for rec in panels.values():
        counts[rec.state] += 1
    return counts


def _dominant_pressure(signals: Sequence[Any]) -> tuple[str | None, int | None]:
    if not signals:
        return None, None
    # signals are PressureSignal; we rely on attribute presence.
    best = None
    for s in signals:
        reason = getattr(s, "reason", None)
        level = getattr(s, "level", None)
        score = (
            float(getattr(s, "energy_pressure", 0.0))
            + float(getattr(s, "mem_pressure", 0.0))
            + float(getattr(s, "util", 0.0))
        )
        candidate = (score, reason, level)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        return None, None
    _score, reason, level = best
    return str(reason) if reason is not None else None, (
        int(level) if level is not None else None
    )


def _mean(values: Iterable[float]) -> float | None:
    vals = list(values)
    if not vals:
        return None
    return sum(vals) / len(vals)


def _safe_pct(after: float | None, before: float | None) -> float | None:
    if after is None or before is None:
        return None
    if before == 0:
        return None
    return (after - before) / before * 100.0


def _delta(
    after: float | None, before: float | None, *, stabilization_time_ticks: int
) -> ComparativeDelta:
    if after is None or before is None:
        return ComparativeDelta(
            absolute_change=None,
            percentage_change=None,
            stabilization_time_ticks=stabilization_time_ticks,
        )
    return ComparativeDelta(
        absolute_change=after - before,
        percentage_change=_safe_pct(after=after, before=before),
        stabilization_time_ticks=stabilization_time_ticks,
    )


def _stability_index(
    *, failed: int, timed_out: int, succeeded: int, running: int, queued: int
) -> float:
    total = max(1, failed + timed_out + succeeded + running + queued)
    bad = failed + timed_out
    # Higher is better; clamp between 0 and 1.
    return max(0.0, min(1.0, 1.0 - (bad / total)))


def _compute_coherence_index(*, succeeded: int, distinct_dedupe_keys: int) -> float:
    # Proxy: fewer distinct dedupe keys implies more coherence.
    if succeeded <= 0:
        return 1.0
    distinct = max(1, distinct_dedupe_keys)
    return max(0.0, min(1.0, 1.0 - (distinct - 1) / succeeded))


def _attempt_map(panels: Mapping[str, PanelExecutionRecord]) -> dict[str, int]:
    return {pid: rec.attempt for pid, rec in panels.items()}


def _state_name(s: PanelState) -> str:
    return str(s.value)


def generate_dt1_field_compute_comparative_report(
    *,
    pre_panels: Mapping[str, PanelExecutionRecord],
    post_panels: Mapping[str, PanelExecutionRecord],
    pre_attempts: Mapping[str, int],
    post_attempts: Mapping[str, int],
    panel_events: Sequence[Any],
    pressure_signals: Sequence[Any],
    pressure_site: str,
    effective_pressure_level: int | None,
    baseline_dominant_pressure_reason: str | None,
    after_dominant_pressure_reason: str | None,
    admission_outcomes_counts: Mapping[str, int],
    credit_leases_by_lane: Mapping[str, Mapping[str, int]],
    tick_duration_ticks: int = 1,
) -> dict[str, Any]:
    """
    Deterministically derives a comparative BEFORE/AFTER field-compute report.

    Notes on evidence limitations (important for integrity):
    - This repo skeleton does not expose true physical power, thermal sensors,
      reservoir/field spill dynamics, or entropy-injection internals.
    - Therefore, required fields are present but may be null or derived from
      available deterministic proxies (credit leases, PressureSignal fields,
      DT1 panel state transitions).
    """
    pre_counts = _count_panels_by_state(pre_panels)
    post_counts = _count_panels_by_state(post_panels)

    pre_succeeded = pre_counts[PanelState.SUCCEEDED]
    post_succeeded = post_counts[PanelState.SUCCEEDED]

    pre_failed = pre_counts[PanelState.FAILED]
    post_failed = post_counts[PanelState.FAILED]

    pre_timed_out = pre_counts[PanelState.TIMED_OUT]
    post_timed_out = post_counts[PanelState.TIMED_OUT]

    pre_running = pre_counts[PanelState.RUNNING]
    post_running = post_counts[PanelState.RUNNING]

    pre_queued = pre_counts[PanelState.QUEUED]
    post_queued = post_counts[PanelState.QUEUED]

    # Proxy throughput / SCO: new successes archiving within this step.
    # Evidence: PanelExecutionRecord.state transitions to SUCCEEDED after step_phase1.
    succeeded_delta = max(0, post_succeeded - pre_succeeded)
    throughput = float(succeeded_delta) / float(max(1, tick_duration_ticks))

    # Stability index: penalize failed/timed_out vs total panel population.
    stability_index_before = _stability_index(
        failed=pre_failed,
        timed_out=pre_timed_out,
        succeeded=pre_succeeded,
        running=pre_running,
        queued=pre_queued,
    )
    stability_index_after = _stability_index(
        failed=post_failed,
        timed_out=post_timed_out,
        succeeded=post_succeeded,
        running=post_running,
        queued=post_queued,
    )

    # Entropy proxy: retry overhead / attempt increments in panels.
    retry_increments = 0
    for pid, post_attempt in post_attempts.items():
        pre_attempt = pre_attempts.get(pid, post_attempt)
        if post_attempt > pre_attempt:
            retry_increments += 1

    queued_after_retry = sum(
        1
        for pid, rec in post_panels.items()
        if rec.state == PanelState.QUEUED
        and post_attempts.get(pid, 0) > pre_attempts.get(pid, 0)
    )

    # Dedupe coherence proxy: use panel state 'succeeded_output' dedupe key count if present.
    # Runtime records succeeded_output=dedupe_success[dedupe_key] but panels don't store dedupe_key.
    # Therefore we proxy coherence with distinct succeeded_output hashes when present.
    distinct_output_fingerprints: set[str] = set()
    for rec in post_panels.values():
        if rec.state == PanelState.SUCCEEDED and rec.succeeded_output is not None:
            distinct_output_fingerprints.add(
                str(hash(frozenset(rec.succeeded_output.items())))
            )
    distinct_dedupe_keys_proxy = len(distinct_output_fingerprints)

    coherence_index_before = _compute_coherence_index(
        succeeded=pre_succeeded, distinct_dedupe_keys=distinct_dedupe_keys_proxy
    )
    coherence_index_after = _compute_coherence_index(
        succeeded=post_succeeded, distinct_dedupe_keys=distinct_dedupe_keys_proxy
    )

    # Thermal saturation proxy: derived from pressure signals.
    util_values = [float(getattr(s, "util", 0.0)) for s in pressure_signals]
    mem_pressure_values = [
        float(getattr(s, "mem_pressure", 0.0)) for s in pressure_signals
    ]
    energy_pressure_values = [
        float(getattr(s, "energy_pressure", 0.0)) for s in pressure_signals
    ]

    max_pressure_proxy = max(
        [0.0] + util_values + mem_pressure_values + energy_pressure_values
    )
    thermal_saturation_pct = max_pressure_proxy * 100.0

    dominant_reason_before = baseline_dominant_pressure_reason
    dominant_reason_after = after_dominant_pressure_reason

    # "Bottleneck migration velocity": proxy absolute difference in effective pressure levels.
    # If effective_pressure_level is not available we set null.
    # We do not have previous effective pressure level inside this call; so only reason migration is computed.
    bottleneck_migration_velocity = None  # missing evidence

    # Compute SWT proxy: throughput * stability_index_after
    swt = throughput * stability_index_after

    # THE proxy: thermal headroom efficiency = stability_index_after / (1 + thermal_saturation_proxy)
    # Stability gain should not occur with thermal saturation collapse; this penalizes high saturation.
    the = (
        stability_index_after / (1.0 + max_pressure_proxy)
        if max_pressure_proxy is not None
        else None
    )

    # Entropy cost ratio proxy: retry_increments per (1 + succeeded_delta)
    ecr = (
        float(retry_increments) / float(1 + succeeded_delta)
        if (1 + succeeded_delta) > 0
        else None
    )

    # Reservoir metrics from credit_leases_by_lane
    # credit_leases_by_lane schema: {lane_key: {"credits_wu": int, "credits_bytes": int, "ttl_ms_remaining": int}}
    stored_energy = sum(
        int(v.get("credits_bytes", 0)) for v in credit_leases_by_lane.values()
    )
    sum(int(v.get("credits_wu", 0)) for v in credit_leases_by_lane.values())
    avg_retention_time = _mean(
        [float(v.get("ttl_ms_remaining", 0)) for v in credit_leases_by_lane.values()]
    )  # ms

    # Admission outcomes as spill/defer proxies
    deferred = int(admission_outcomes_counts.get("DEFERRED", 0))
    rejected = int(admission_outcomes_counts.get("REJECTED", 0))
    admitted = int(admission_outcomes_counts.get("ACCEPTED", 0)) + int(
        admission_outcomes_counts.get("PUBLISHED", 0)
    )

    # Spill rates proxy: use rejected+deferred as "spill pressure" in this skeleton.
    spill_rate_proxy = float(deferred + rejected) / float(
        max(1, admitted + deferred + rejected)
    )

    # Failures mapping
    failure_events = [
        e
        for e in panel_events
        if getattr(e, "event_type", None) in {"failed", "timed_out"}
    ]
    failure_map = {
        "failed_events": len(
            [e for e in failure_events if getattr(e, "event_type", None) == "failed"]
        ),
        "timed_out_events": len(
            [e for e in failure_events if getattr(e, "event_type", None) == "timed_out"]
        ),
        "affected_panel_count_delta": max(
            0, post_failed + post_timed_out - pre_failed - pre_timed_out
        ),
        "stability_impact": {
            "stability_index_before": stability_index_before,
            "stability_index_after": stability_index_after,
        },
    }

    bottleneck_migration_map = {
        "pressure_site": pressure_site,
        "effective_pressure_level": effective_pressure_level,
        "dominant_pressure_reason_before": dominant_reason_before,
        "dominant_pressure_reason_after": dominant_reason_after,
        "bottleneck_migration_velocity_proxy": bottleneck_migration_velocity,
    }

    entropy_delta_proxy_increased = ecr is not None and ecr > 0.0
    coherence_delta = coherence_index_after - coherence_index_before
    resilience_delta_margin_proxy = stability_index_after - stability_index_before

    interpretation = {
        "why_bottleneck_moved": (
            "Derived from dominant PressureSignal reason migration "
            f"({dominant_reason_before!r} -> {dominant_reason_after!r}) and throughput/stability tradeoff proxies."
        ),
        "new_equilibrium": (
            "POST-EQUILIBRIUM is computed from panel state transitions "
            f"(succeeded_delta={succeeded_delta}, retry_increments={retry_increments})."
        ),
        "entropy_trend_proxy": (
            "increased" if entropy_delta_proxy_increased else "decreased_or_zero_proxy"
        ),
        "field_coherence_trend_proxy": "up" if coherence_delta >= 0 else "down",
        "resilience_margin_proxy": (
            "gained" if resilience_delta_margin_proxy >= 0 else "lost"
        ),
        "evidence_gaps": "Physical power/thermal sensors, reservoir spill timing, and true entropy injection are not exposed in this repo; values are proxy-derived or null.",
    }

    # Phase buckets
    pre_phase = ReportPhase(
        core_compute_metrics={
            "SCO": None,
            "Throughput": None,
            "CPW": None,
            "SWT": None,
            "THE": None,
        },
        stability_metrics={
            "stability index": stability_index_before,
            "recovery half-life": None,
            "variance absorption ratio": 1.0
            - (pre_failed + pre_timed_out)
            / float(
                max(
                    1,
                    pre_succeeded
                    + pre_failed
                    + pre_timed_out
                    + pre_running
                    + pre_queued,
                )
            ),
            "coherence index": coherence_index_before,
            "bottleneck migration velocity": None,
        },
        entropy_metrics={
            "straggler count": float(pre_running + pre_queued),  # proxy
            "queue oscillation": float(0),
            "gearbox chatter frequency": float(queued_after_retry),
            "spill instability events": None,
            "retry/retransmit overhead": float(retry_increments),
            "ocp trigger frequency": None,
            "entropy cost ratio (ECR)": ecr,
        },
        reservoir_field_metrics={
            "reservoir levels by domain": None,
            "retention time": avg_retention_time,
            "spill rates": None,
            "head pressure distribution": None,
            "potential energy utilization (PEU)": None,
            "total stored field energy": float(stored_energy),
            "overflow propagation timing": None,
        },
        power_thermal_metrics={
            "total system power": None,
            "per-domain power": None,
            "rail saturation levels": None,
            "thermal saturation %": thermal_saturation_pct,
            "hotspot migration": None,
            "efficiency under transient load": None,
        },
        failure_propagation_map={},
        bottleneck_migration_analysis={},
        interpretation={},
    )

    transient_phase = ReportPhase(
        core_compute_metrics={
            "SCO": float(succeeded_delta),
            "Throughput": float(throughput),
            "CPW": None,
            "SWT": float(swt),
            "THE": the,
        },
        stability_metrics={
            "stability index": stability_index_after,
            "recovery half-life": None,
            "variance absorption ratio": 1.0
            - (post_failed + post_timed_out)
            / float(
                max(
                    1,
                    post_succeeded
                    + post_failed
                    + post_timed_out
                    + post_running
                    + post_queued,
                )
            ),
            "coherence index": coherence_index_after,
            "bottleneck migration velocity": None,
        },
        entropy_metrics={
            "straggler count": float(post_running + post_queued),
            "queue oscillation": float(queued_after_retry),
            "gearbox chatter frequency": float(queued_after_retry),
            "spill instability events": None,
            "retry/retransmit overhead": float(retry_increments),
            "ocp trigger frequency": None,
            "entropy cost ratio (ECR)": ecr,
        },
        reservoir_field_metrics={
            "reservoir levels by domain": {
                lane: {
                    "credits_wu": int(v.get("credits_wu", 0)),
                    "credits_bytes": int(v.get("credits_bytes", 0)),
                    "ttl_ms_remaining": int(v.get("ttl_ms_remaining", 0)),
                }
                for lane, v in credit_leases_by_lane.items()
            },
            "retention time": avg_retention_time,
            "spill rates": spill_rate_proxy,
            "head pressure distribution": {
                "util_mean": _mean(util_values),
                "mem_pressure_mean": _mean(mem_pressure_values),
                "energy_pressure_mean": _mean(energy_pressure_values),
            },
            "potential energy utilization (PEU)": None,
            "total stored field energy": float(stored_energy),
            "overflow propagation timing": None,
        },
        power_thermal_metrics={
            "total system power": None,
            "per-domain power": None,
            "rail saturation levels": None,
            "thermal saturation %": thermal_saturation_pct,
            "hotspot migration": None,
            "efficiency under transient load": the,
        },
        failure_propagation_map=failure_map,
        bottleneck_migration_analysis=bottleneck_migration_map,
        interpretation=interpretation,
    )

    post_phase = ReportPhase(
        core_compute_metrics={
            "SCO": float(succeeded_delta),
            "Throughput": float(throughput),
            "CPW": None,
            "SWT": float(swt),
            "THE": the,
        },
        stability_metrics={
            "stability index": stability_index_after,
            "recovery half-life": None,
            "variance absorption ratio": 1.0
            - (post_failed + post_timed_out)
            / float(
                max(
                    1,
                    post_succeeded
                    + post_failed
                    + post_timed_out
                    + post_running
                    + post_queued,
                )
            ),
            "coherence index": coherence_index_after,
            "bottleneck migration velocity": None,
        },
        entropy_metrics={
            "straggler count": float(post_running + post_queued),
            "queue oscillation": float(queued_after_retry),
            "gearbox chatter frequency": float(queued_after_retry),
            "spill instability events": None,
            "retry/retransmit overhead": float(retry_increments),
            "ocp trigger frequency": None,
            "entropy cost ratio (ECR)": ecr,
        },
        reservoir_field_metrics={
            "reservoir levels by domain": {
                lane: {
                    "credits_wu": int(v.get("credits_wu", 0)),
                    "credits_bytes": int(v.get("credits_bytes", 0)),
                    "ttl_ms_remaining": int(v.get("ttl_ms_remaining", 0)),
                }
                for lane, v in credit_leases_by_lane.items()
            },
            "retention time": avg_retention_time,
            "spill rates": spill_rate_proxy,
            "head pressure distribution": {
                "util_mean": _mean(util_values),
                "mem_pressure_mean": _mean(mem_pressure_values),
                "energy_pressure_mean": _mean(energy_pressure_values),
            },
            "potential energy utilization (PEU)": None,
            "total stored field energy": float(stored_energy),
            "overflow propagation timing": None,
        },
        power_thermal_metrics={
            "total system power": None,
            "per-domain power": None,
            "rail saturation levels": None,
            "thermal saturation %": thermal_saturation_pct,
            "hotspot migration": None,
            "efficiency under transient load": the,
        },
        failure_propagation_map=failure_map,
        bottleneck_migration_analysis=bottleneck_migration_map,
        interpretation=interpretation,
    )

    # Deltas (numeric only). Stabilization time is tick_duration_ticks.
    stabilization_time_ticks = tick_duration_ticks
    delta_analysis: dict[str, Any] = {
        "core_compute_metrics": {
            "SCO": _delta(
                post_phase.core_compute_metrics["SCO"],
                pre_phase.core_compute_metrics["SCO"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "Throughput": _delta(
                post_phase.core_compute_metrics["Throughput"],
                pre_phase.core_compute_metrics["Throughput"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "CPW": _delta(
                post_phase.core_compute_metrics["CPW"],
                pre_phase.core_compute_metrics["CPW"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "SWT": _delta(
                post_phase.core_compute_metrics["SWT"],
                pre_phase.core_compute_metrics["SWT"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "THE": _delta(
                post_phase.core_compute_metrics["THE"],
                pre_phase.core_compute_metrics["THE"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
        },
        "stability_metrics": {
            "stability index": _delta(
                post_phase.stability_metrics["stability index"],
                pre_phase.stability_metrics["stability index"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "recovery half-life": _delta(
                post_phase.stability_metrics["recovery half-life"],
                pre_phase.stability_metrics["recovery half-life"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "variance absorption ratio": _delta(
                post_phase.stability_metrics["variance absorption ratio"],
                pre_phase.stability_metrics["variance absorption ratio"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "coherence index": _delta(
                post_phase.stability_metrics["coherence index"],
                pre_phase.stability_metrics["coherence index"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "bottleneck migration velocity": _delta(
                post_phase.stability_metrics["bottleneck migration velocity"],
                pre_phase.stability_metrics["bottleneck migration velocity"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
        },
        "entropy_metrics": {
            "straggler count": _delta(
                post_phase.entropy_metrics["straggler count"],
                pre_phase.entropy_metrics["straggler count"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "queue oscillation": _delta(
                post_phase.entropy_metrics["queue oscillation"],
                pre_phase.entropy_metrics["queue oscillation"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "gearbox chatter frequency": _delta(
                post_phase.entropy_metrics["gearbox chatter frequency"],
                pre_phase.entropy_metrics["gearbox chatter frequency"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "spill instability events": _delta(
                post_phase.entropy_metrics["spill instability events"],
                pre_phase.entropy_metrics["spill instability events"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "retry/retransmit overhead": _delta(
                post_phase.entropy_metrics["retry/retransmit overhead"],
                pre_phase.entropy_metrics["retry/retransmit overhead"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "ocp trigger frequency": _delta(
                post_phase.entropy_metrics["ocp trigger frequency"],
                pre_phase.entropy_metrics["ocp trigger frequency"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "entropy cost ratio (ECR)": _delta(
                post_phase.entropy_metrics["entropy cost ratio (ECR)"],
                pre_phase.entropy_metrics["entropy cost ratio (ECR)"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
        },
        "reservoir_field_metrics": {
            "reservoir levels by domain": {
                "absolute_change": None,
                "percentage_change": None,
                "stabilization_time_ticks": stabilization_time_ticks,
            },
            "retention time": _delta(
                post_phase.reservoir_field_metrics["retention time"],
                pre_phase.reservoir_field_metrics["retention time"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "spill rates": _delta(
                post_phase.reservoir_field_metrics["spill rates"],
                pre_phase.reservoir_field_metrics["spill rates"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "head pressure distribution": {
                "absolute_change": None,
                "percentage_change": None,
                "stabilization_time_ticks": stabilization_time_ticks,
            },
            "potential energy utilization (PEU)": _delta(
                post_phase.reservoir_field_metrics[
                    "potential energy utilization (PEU)"
                ],
                pre_phase.reservoir_field_metrics["potential energy utilization (PEU)"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "total stored field energy": _delta(
                post_phase.reservoir_field_metrics["total stored field energy"],
                pre_phase.reservoir_field_metrics["total stored field energy"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "overflow propagation timing": {
                "absolute_change": None,
                "percentage_change": None,
                "stabilization_time_ticks": stabilization_time_ticks,
            },
        },
        "power_thermal_metrics": {
            "total system power": {
                "absolute_change": None,
                "percentage_change": None,
                "stabilization_time_ticks": stabilization_time_ticks,
            },
            "per-domain power": {
                "absolute_change": None,
                "percentage_change": None,
                "stabilization_time_ticks": stabilization_time_ticks,
            },
            "rail saturation levels": {
                "absolute_change": None,
                "percentage_change": None,
                "stabilization_time_ticks": stabilization_time_ticks,
            },
            "thermal saturation %": _delta(
                post_phase.power_thermal_metrics["thermal saturation %"],
                pre_phase.power_thermal_metrics["thermal saturation %"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
            "hotspot migration": {
                "absolute_change": None,
                "percentage_change": None,
                "stabilization_time_ticks": stabilization_time_ticks,
            },
            "efficiency under transient load": _delta(
                post_phase.power_thermal_metrics["efficiency under transient load"],
                pre_phase.power_thermal_metrics["efficiency under transient load"],
                stabilization_time_ticks=stabilization_time_ticks,
            ).__dict__,
        },
    }

    report: dict[str, Any] = {
        "PRE-EVENT baseline": {
            "core compute metrics": pre_phase.core_compute_metrics,
            "stability metrics": pre_phase.stability_metrics,
            "entropy metrics": pre_phase.entropy_metrics,
            "reservoir / field metrics": pre_phase.reservoir_field_metrics,
            "power & thermal metrics": pre_phase.power_thermal_metrics,
        },
        "TRANSIENT phase": {
            "core compute metrics": transient_phase.core_compute_metrics,
            "stability metrics": transient_phase.stability_metrics,
            "entropy metrics": transient_phase.entropy_metrics,
            "reservoir / field metrics": transient_phase.reservoir_field_metrics,
            "power & thermal metrics": transient_phase.power_thermal_metrics,
            "failure propagation map": transient_phase.failure_propagation_map,
            "bottleneck migration analysis": transient_phase.bottleneck_migration_analysis,
        },
        "POST-EQUILIBRIUM state": {
            "core compute metrics": post_phase.core_compute_metrics,
            "stability metrics": post_phase.stability_metrics,
            "entropy metrics": post_phase.entropy_metrics,
            "reservoir / field metrics": post_phase.reservoir_field_metrics,
            "power & thermal metrics": post_phase.power_thermal_metrics,
            "failure propagation map": post_phase.failure_propagation_map,
            "bottleneck migration analysis": post_phase.bottleneck_migration_analysis,
        },
        "DELTA analysis": delta_analysis,
        "Failure propagation map": failure_map,
        "Bottleneck migration analysis": bottleneck_migration_map,
        "interpretation layer": interpretation,
    }
    return report


REQUIRED_CORE_COMPUTE_METRICS = {
    "SCO",
    "Throughput",
    "Compute-per-Watt (CPW)",  # legacy key; may be null if missing
    "CPW",
    "Stability-Weighted Throughput (SWT)",
    "SWT",
    "Thermal Headroom Efficiency (THE)",
    "THE",
}

# We validate against the concrete keys used by generate_dt1_field_compute_comparative_report.
# This constant is intentionally strict so reports cannot silently omit sections.
REQUIRED_REPORT_TOP_KEYS = {
    "PRE-EVENT baseline",
    "TRANSIENT phase",
    "POST-EQUILIBRIUM state",
    "DELTA analysis",
    "Failure propagation map",
    "Bottleneck migration analysis",
    "interpretation layer",
}

REQUIRED_BUCKET_KEYS = {
    "core compute metrics",
    "stability metrics",
    "entropy metrics",
    "reservoir / field metrics",
    "power & thermal metrics",
}


def validate_dt1_field_compute_comparative_report(  # noqa: C901
    report: Mapping[str, Any],
) -> None:
    missing_top = [k for k in REQUIRED_REPORT_TOP_KEYS if k not in report]
    if missing_top:
        raise ValueError(f"Missing top-level keys in dt1 report: {missing_top}")

    for phase_key in [
        "PRE-EVENT baseline",
        "TRANSIENT phase",
        "POST-EQUILIBRIUM state",
    ]:
        phase = report[phase_key]
        for bucket in REQUIRED_BUCKET_KEYS:
            if bucket not in phase:
                raise ValueError(f"Missing bucket {bucket!r} under {phase_key!r}")
        # Required metric keys exist; values may be None.
        core = phase["core compute metrics"]
        stability = phase["stability metrics"]
        entropy = phase["entropy metrics"]
        reservoir = phase["reservoir / field metrics"]
        power = phase["power & thermal metrics"]

        required_core = {"SCO", "Throughput", "CPW", "SWT", "THE"}
        required_stability = {
            "stability index",
            "recovery half-life",
            "variance absorption ratio",
            "coherence index",
            "bottleneck migration velocity",
        }
        required_entropy = {
            "straggler count",
            "queue oscillation",
            "gearbox chatter frequency",
            "spill instability events",
            "retry/retransmit overhead",
            "ocp trigger frequency",
            "entropy cost ratio (ECR)",
        }
        required_reservoir = {
            "reservoir levels by domain",
            "retention time",
            "spill rates",
            "head pressure distribution",
            "potential energy utilization (PEU)",
            "total stored field energy",
            "overflow propagation timing",
        }
        required_power = {
            "total system power",
            "per-domain power",
            "rail saturation levels",
            "thermal saturation %",
            "hotspot migration",
            "efficiency under transient load",
        }

        for name, src, req in [
            ("core compute metrics", core, required_core),
            ("stability metrics", stability, required_stability),
            ("entropy metrics", entropy, required_entropy),
            ("reservoir / field metrics", reservoir, required_reservoir),
            ("power & thermal metrics", power, required_power),
        ]:
            missing = [k for k in req if k not in src]
            if missing:
                raise ValueError(f"Missing metric keys {missing} under {name}")

    # DELTA structure: ensure numeric deltas have the required shape.
    delta = report["DELTA analysis"]
    for group_key in [
        "core_compute_metrics",
        "stability_metrics",
        "entropy_metrics",
        "reservoir_field_metrics",
        "power_thermal_metrics",
    ]:
        if group_key not in delta:
            raise ValueError(f"Missing DELTA group {group_key!r}")
        group = delta[group_key]
        for metric_name, v in group.items():
            if isinstance(v, dict) and "absolute_change" in v:
                for leaf_key in [
                    "absolute_change",
                    "percentage_change",
                    "stabilization_time_ticks",
                ]:
                    if leaf_key not in v:
                        raise ValueError(
                            f"DELTA leaf for {group_key}.{metric_name} missing {leaf_key!r}: {v}"
                        )
            # Allow structured dict leaves for composite fields (e.g. reservoir levels)
