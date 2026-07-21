from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from egregore.dt1.models import PressureReason, PressureSignal


@dataclass(frozen=True)
class PressureAggregate:
    scope: str  # edge | cluster
    site: str
    considered_signals: int
    raw_level: int
    dominant_reason: PressureReason
    queue_depth_wu: int
    queue_depth_bytes: int


@dataclass(frozen=True)
class PressureDebounceState:
    effective_level: int = 0
    up_ticks: int = 0
    down_ticks: int = 0


def _bounded_level(level: int) -> int:
    if level < 0:
        return 0
    if level > 3:
        return 3
    return level


def _level_from_utilization(
    *, util: float, mem_pressure: float, energy_pressure: float
) -> int:
    max_signal = max(util, mem_pressure, energy_pressure)
    if max_signal >= 0.95:
        return 3
    if max_signal >= 0.80:
        return 2
    if max_signal >= 0.60:
        return 1
    return 0


def aggregate_pressure_level(
    *,
    signals: Sequence[PressureSignal],
    site: str,
    scope: str = "edge",
) -> PressureAggregate:
    """
    Deterministic pressure aggregation.

    - scope=edge: consider only matching site signals
    - scope=cluster: consider all signals
    """
    if scope not in {"edge", "cluster"}:
        raise ValueError("scope must be 'edge' or 'cluster'")

    if scope == "edge":
        selected = [s for s in signals if s.site == site]
    else:
        selected = list(signals)

    if not selected:
        return PressureAggregate(
            scope=scope,
            site=site,
            considered_signals=0,
            raw_level=0,
            dominant_reason=PressureReason.PRESSURE_UNSPECIFIED,
            queue_depth_wu=0,
            queue_depth_bytes=0,
        )

    # Aggregate queue depth additively to capture pressure mass.
    queue_depth_wu = sum(max(0, s.queue_depth_wu) for s in selected)
    queue_depth_bytes = sum(max(0, s.queue_depth_bytes) for s in selected)

    raw_metric_level = max(
        _level_from_utilization(
            util=s.util,
            mem_pressure=s.mem_pressure,
            energy_pressure=s.energy_pressure,
        )
        for s in selected
    )
    raw_signal_level = max(_bounded_level(s.level) for s in selected)
    raw_level = max(raw_metric_level, raw_signal_level)

    # Dominant reason is selected deterministically from max-level candidates.
    ranked = sorted(
        selected,
        key=lambda s: (
            -_bounded_level(s.level),
            -s.ts_unix_nanos,
            int(s.reason),
            s.stage_id,
            s.site,
        ),
    )
    dominant_reason = ranked[0].reason

    return PressureAggregate(
        scope=scope,
        site=site,
        considered_signals=len(selected),
        raw_level=raw_level,
        dominant_reason=dominant_reason,
        queue_depth_wu=queue_depth_wu,
        queue_depth_bytes=queue_depth_bytes,
    )


def apply_pressure_hysteresis(
    *,
    previous: PressureDebounceState,
    raw_level: int,
    upshift_ticks_required: int = 1,
    downshift_ticks_required: int = 2,
) -> PressureDebounceState:
    """
    Deterministic debounce for pressure flapping.

    Upshift and downshift thresholds can differ to provide hysteresis.
    """
    if upshift_ticks_required <= 0:
        raise ValueError("upshift_ticks_required must be >= 1")
    if downshift_ticks_required <= 0:
        raise ValueError("downshift_ticks_required must be >= 1")

    bounded_raw = _bounded_level(raw_level)
    current = _bounded_level(previous.effective_level)

    if bounded_raw == current:
        return PressureDebounceState(effective_level=current, up_ticks=0, down_ticks=0)

    if bounded_raw > current:
        next_up_ticks = previous.up_ticks + 1
        if next_up_ticks >= upshift_ticks_required:
            return PressureDebounceState(
                effective_level=bounded_raw, up_ticks=0, down_ticks=0
            )
        return PressureDebounceState(
            effective_level=current,
            up_ticks=next_up_ticks,
            down_ticks=0,
        )

    next_down_ticks = previous.down_ticks + 1
    if next_down_ticks >= downshift_ticks_required:
        return PressureDebounceState(
            effective_level=bounded_raw, up_ticks=0, down_ticks=0
        )
    return PressureDebounceState(
        effective_level=current,
        up_ticks=0,
        down_ticks=next_down_ticks,
    )
