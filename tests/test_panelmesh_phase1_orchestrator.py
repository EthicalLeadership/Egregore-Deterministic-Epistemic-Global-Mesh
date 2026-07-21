from __future__ import annotations

from egregore.dt1.models import (
    CreditGrant,
    Dt1Class,
    PressureReason,
    PressureSignal,
    Priority,
    RoutingHint,
    SpanRef,
    TraceContext,
    WorkSpanKind,
    WorkUnit,
    WorkUnitEnvelope,
)
from egregore.dt1.panelmesh_phase1_orchestrator import (
    CreditLeaseUpdate,
    OrchestratorConfig,
    PanelmeshPhase1Orchestrator,
)
from egregore.dt1.panelmesh_phase1_runtime import NodeCapability, PanelState


def _mk_workunit(
    *,
    wu_id_hi: int = 1,
    wu_id_lo: int = 1,
    priority: Priority = Priority.P1,
    deadline_unix_nanos: int = 10_000,
) -> WorkUnit:
    env = WorkUnitEnvelope(
        wu_id_hi=wu_id_hi,
        wu_id_lo=wu_id_lo,
        tenant_id=1,
        dt1_class=Dt1Class.DT1_CLASS_L,
        dt1_type="A",
        priority=priority,
        deadline_unix_nanos=deadline_unix_nanos,
        est_cost_bucket=0,
        routing=RoutingHint.ROUTING_CORE_OK,
        trace=TraceContext(trace_id_hi=0, trace_id_lo=0, span_id=0, sampled=True),
        flags=0,
        attempt=0,
    )

    spans = (SpanRef(kind=WorkSpanKind.KIND_UNSPECIFIED, index=0, length_bytes=0),)
    return WorkUnit(env=env, spans=spans)


def _mk_pressure_signals(*, site: str, level: int = 1) -> tuple[PressureSignal, ...]:
    return (
        PressureSignal(
            stage_id="cqb",
            site=site,
            reason=PressureReason.PRESSURE_CPU,
            level=level,
            queue_depth_wu=0,
            queue_depth_bytes=0,
            util=0.1,
            mem_pressure=0.1,
            energy_pressure=0.1,
            ts_unix_nanos=1,
        ),
    )


def _mk_nodes() -> list[NodeCapability]:
    return [
        NodeCapability(node_id="n1", tags={"gpu": "A10", "region": "qc1"}),
        NodeCapability(node_id="n2", tags={"gpu": "V100", "region": "qc1"}),
    ]


def test_orchestrator_credit_grant_accepts_and_completes_next_tick() -> None:
    nodes = _mk_nodes()
    site = "mtl01"

    wu = _mk_workunit(deadline_unix_nanos=20_000)

    from egregore.dt1.panelmesh_phase1_runtime import RetryPolicy

    cfg = OrchestratorConfig(
        lease_ttl_ticks=1,
        panel_timeout_ticks=10,
        panel_retry_policy=RetryPolicy(max_attempts=2, backoff_ticks=1),
        panel_sla_ms=120,
        panel_model_id="dt1-model",
        need_wu=1,
        need_bytes=0,
        min_defer_slack_nanos=0,
        retry_after_ms=0,
        edge_can_execute=True,
    )

    orch = PanelmeshPhase1Orchestrator(
        config=cfg,
        nodes=nodes,
        initial_workunits=[wu],
    )

    lane_key = orch.lane_key_for_workunit(site=site, wu=wu)

    credit_grant = CreditGrant(
        stage_id="cqb",
        site=site,
        dt1_type=wu.env.dt1_type,
        priority=wu.env.priority,
        credits_wu=5,
        credits_bytes=0,
        ttl_ms=1000,
        epoch=1,
    )

    # Tick 0: credit lease becomes ACTIVE, ES admission should accept, panels leased+dispatched,
    # but runner is not invoked until tick 1 (per panelmesh runtime test semantics).
    events0 = orch.tick(
        now_unix_nanos=1000,
        pressure_signals=_mk_pressure_signals(site=site, level=0),
        pressure_site=site,
        critical=False,
        credit_lease_updates={
            lane_key: CreditLeaseUpdate(
                ttl_expired=False, grant=credit_grant, revoke=None
            )
        },
    )

    assert len(orch.phase1_state.panels) == 1
    panel = next(iter(orch.phase1_state.panels.values()))
    assert panel.state == PanelState.RUNNING

    assert any(e.event_type == "leased" for e in events0)
    assert any(e.event_type == "dispatched" for e in events0)
    assert not any(e.event_type == "completed" for e in events0)

    # Tick 1: runner invoked, panel archived.
    events1 = orch.tick(
        now_unix_nanos=2000,
        pressure_signals=_mk_pressure_signals(site=site, level=0),
        pressure_site=site,
        critical=False,
        credit_lease_updates={},
    )

    assert len(orch.phase1_state.panels) == 1
    panel = next(iter(orch.phase1_state.panels.values()))
    assert panel.state == PanelState.ARCHIVED
    assert panel.succeeded_output is not None

    assert any(e.event_type == "completed" for e in events1)
