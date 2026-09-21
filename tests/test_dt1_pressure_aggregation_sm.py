from egregore.dt1.models import PressureReason, PressureSignal
from egregore.dt1.state_machines.es_admission_sm import (
    EsPressurePolicy,
    resolve_es_pressure_level,
)
from egregore.dt1.state_machines.pressure_aggregation_sm import (
    PressureDebounceState,
    aggregate_pressure_level,
    apply_pressure_hysteresis,
)


def _signal(
    *, site: str, level: int, util: float, reason: PressureReason, ts: int
) -> PressureSignal:
    return PressureSignal(
        stage_id="cqb",
        site=site,
        reason=reason,
        level=level,
        queue_depth_wu=10,
        queue_depth_bytes=100,
        util=util,
        mem_pressure=0.1,
        energy_pressure=0.1,
        ts_unix_nanos=ts,
    )


def test_aggregate_pressure_edge_scope_filters_by_site() -> None:
    signals = (
        _signal(
            site="mtl01", level=1, util=0.65, reason=PressureReason.PRESSURE_CPU, ts=10
        ),
        _signal(
            site="yyz01", level=3, util=0.99, reason=PressureReason.PRESSURE_GPU, ts=20
        ),
    )

    edge = aggregate_pressure_level(signals=signals, site="mtl01", scope="edge")
    assert edge.considered_signals == 1
    assert edge.raw_level == 1
    assert edge.dominant_reason == PressureReason.PRESSURE_CPU


def test_aggregate_pressure_cluster_scope_includes_all_sites() -> None:
    signals = (
        _signal(
            site="mtl01", level=1, util=0.65, reason=PressureReason.PRESSURE_CPU, ts=10
        ),
        _signal(
            site="yyz01", level=2, util=0.99, reason=PressureReason.PRESSURE_GPU, ts=20
        ),
    )

    cluster = aggregate_pressure_level(signals=signals, site="mtl01", scope="cluster")
    assert cluster.considered_signals == 2
    assert cluster.raw_level == 3
    assert cluster.dominant_reason == PressureReason.PRESSURE_GPU


def test_apply_pressure_hysteresis_requires_consecutive_ticks() -> None:
    state = PressureDebounceState(effective_level=1)

    state = apply_pressure_hysteresis(
        previous=state,
        raw_level=3,
        upshift_ticks_required=2,
        downshift_ticks_required=3,
    )
    assert state.effective_level == 1
    assert state.up_ticks == 1

    state = apply_pressure_hysteresis(
        previous=state,
        raw_level=3,
        upshift_ticks_required=2,
        downshift_ticks_required=3,
    )
    assert state.effective_level == 3
    assert state.up_ticks == 0

    state = apply_pressure_hysteresis(
        previous=state,
        raw_level=1,
        upshift_ticks_required=2,
        downshift_ticks_required=3,
    )
    assert state.effective_level == 3
    assert state.down_ticks == 1

    state = apply_pressure_hysteresis(
        previous=state,
        raw_level=1,
        upshift_ticks_required=2,
        downshift_ticks_required=3,
    )
    assert state.effective_level == 3
    assert state.down_ticks == 2

    state = apply_pressure_hysteresis(
        previous=state,
        raw_level=1,
        upshift_ticks_required=2,
        downshift_ticks_required=3,
    )
    assert state.effective_level == 1
    assert state.down_ticks == 0


def test_resolve_es_pressure_level_applies_policy_hysteresis() -> None:
    signals = (
        _signal(
            site="mtl01", level=3, util=0.91, reason=PressureReason.PRESSURE_CPU, ts=10
        ),
    )
    previous = PressureDebounceState(effective_level=1)

    level, next_state, aggregate = resolve_es_pressure_level(
        signals=signals,
        site="mtl01",
        previous=previous,
        policy=EsPressurePolicy(
            scope="edge", upshift_ticks_required=2, downshift_ticks_required=2
        ),
    )
    assert aggregate.raw_level == 3
    assert level == 1
    assert next_state.up_ticks == 1
