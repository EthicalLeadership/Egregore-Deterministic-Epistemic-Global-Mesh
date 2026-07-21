from __future__ import annotations

from egregore.dt1.panelmesh_phase1_runtime import (
    NodeCapability,
    Panel,
    PanelState,
    RetryPolicy,
    RunnerResult,
    TickInputs,
    create_job_panels,
    step_phase1,
)


def _mk_runner(*, behavior: str):
    """
    behavior:
    - "ok": returns success output
    - "fail_once": raises on first call per (panel_id), then succeeds
    - "always_fail": always raises
    """
    calls: dict[str, int] = {}

    def _runner(panel: Panel, node: NodeCapability) -> RunnerResult:
        calls[panel.panel_id] = calls.get(panel.panel_id, 0) + 1
        if behavior == "ok":
            return RunnerResult(
                output={"node_id": node.node_id, "panel_id": panel.panel_id}
            )
        if behavior == "always_fail":
            raise RuntimeError("runner_fail")
        if behavior == "fail_once":
            if calls[panel.panel_id] == 1:
                raise RuntimeError("runner_fail_first")
            return RunnerResult(
                output={"node_id": node.node_id, "panel_id": panel.panel_id}
            )
        raise ValueError(f"unknown behavior: {behavior}")

    return _runner, calls


def _mk_nodes() -> list[NodeCapability]:
    return [
        NodeCapability(node_id="n1", tags={"gpu": "A10", "region": "qc1"}),
        NodeCapability(node_id="n2", tags={"gpu": "V100", "region": "qc1"}),
    ]


def _mk_panel(
    *,
    panel_id: str,
    job_id: str,
    dedupe_key: str,
    affinity: dict[str, object] | None = None,
    retry: RetryPolicy | None = None,
    timeout_ticks: int = 10,
) -> Panel:
    return Panel(
        panel_id=panel_id,
        job_id=job_id,
        priority=1,
        sla_ms=120,
        model_id="llm-v1",
        affinity=affinity or {},
        payload={"input": panel_id},
        dedupe_key=dedupe_key,
        timeout_ticks=timeout_ticks,
        retry=retry or RetryPolicy(max_attempts=2, backoff_ticks=1),
    )


def test_rr_leasing_then_completion_ok() -> None:
    nodes = _mk_nodes()

    panel = _mk_panel(
        panel_id="p1",
        job_id="j1",
        dedupe_key="d1",
        affinity={"region": "qc1"},
    )

    runner, calls = _mk_runner(behavior="ok")
    state = create_job_panels(job_id="j1", panels=[panel])

    inputs = TickInputs(runner=runner, nodes=nodes, lease_ttl_ticks=1)

    # tick 0 -> lease (queued->leased)
    state, events = step_phase1(state=state, panels={"p1": panel}, inputs=inputs)
    assert state.tick == 1
    assert state.panels["p1"].state == PanelState.RUNNING
    assert any(e.event_type == "leased" for e in events)
    assert any(e.event_type == "dispatched" for e in events)

    # tick 1 -> runner invoked -> succeeded/archived
    state, events = step_phase1(state=state, panels={"p1": panel}, inputs=inputs)
    rec = state.panels["p1"]
    assert rec.state == PanelState.ARCHIVED
    assert rec.succeeded_output is not None
    assert rec.succeeded_output["panel_id"] == "p1"

    # runner invoked exactly once
    assert calls == {"p1": 1}
    assert any(e.event_type == "completed" for e in events)


def test_dedupe_success_caches_output() -> None:
    nodes = _mk_nodes()

    p1 = _mk_panel(
        panel_id="p1", job_id="j1", dedupe_key="same", affinity={"region": "qc1"}
    )
    p2 = _mk_panel(
        panel_id="p2", job_id="j1", dedupe_key="same", affinity={"region": "qc1"}
    )

    runner, calls = _mk_runner(behavior="ok")
    state = create_job_panels(job_id="j1", panels=[p1, p2])

    inputs = TickInputs(runner=runner, nodes=nodes, lease_ttl_ticks=1)

    # Tick 0: both queued will be leased and dispatched (same tick)
    state, _events0 = step_phase1(
        state=state,
        panels={"p1": p1, "p2": p2},
        inputs=inputs,
    )
    assert state.panels["p1"].state == PanelState.RUNNING
    assert state.panels["p2"].state == PanelState.RUNNING

    # Tick 1: runner invoked for p1, then p2 should be completed via dedupe_success
    state, _events1 = step_phase1(
        state=state,
        panels={"p1": p1, "p2": p2},
        inputs=inputs,
    )

    assert state.panels["p1"].state == PanelState.ARCHIVED
    assert state.panels["p2"].state == PanelState.ARCHIVED

    # runner called only once because dedupe_key hits for p2.
    assert calls == {"p1": 1}


def test_retry_on_runner_failure_then_failed_archived() -> None:
    nodes = _mk_nodes()

    p1 = _mk_panel(
        panel_id="p1",
        job_id="j1",
        dedupe_key="d1",
        affinity={"region": "qc1"},
        retry=RetryPolicy(max_attempts=2, backoff_ticks=1),
    )

    runner, calls = _mk_runner(behavior="always_fail")
    state = create_job_panels(job_id="j1", panels=[p1])
    inputs = TickInputs(runner=runner, nodes=nodes, lease_ttl_ticks=1)

    # Tick 0: queued->leased->running; dispatched event emitted
    state, _ = step_phase1(state=state, panels={"p1": p1}, inputs=inputs)

    # Tick 1: running -> runner fails attempt 0 -> backoff -> queued(attempt=1)
    state, _events = step_phase1(state=state, panels={"p1": p1}, inputs=inputs)
    rec1 = state.panels["p1"]
    assert rec1.state == PanelState.QUEUED
    assert rec1.attempt == 1

    # Tick 2: queued eligible again -> lease->running
    state, _ = step_phase1(state=state, panels={"p1": p1}, inputs=inputs)
    assert state.panels["p1"].state == PanelState.RUNNING

    # Tick 3: second failure exhausts attempts -> failed->archived
    state, _ = step_phase1(state=state, panels={"p1": p1}, inputs=inputs)
    rec2 = state.panels["p1"]
    assert rec2.state == PanelState.ARCHIVED
    assert rec2.failure_reason is not None
    assert "runner_fail" in rec2.failure_reason or rec2.failure_reason.startswith(
        "runner_fail"
    )

    # called twice total
    assert calls == {"p1": 2}
