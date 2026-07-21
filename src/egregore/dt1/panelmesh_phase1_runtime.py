from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from enum import StrEnum


class PanelState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    # Backoff expressed in deterministic ticks; pure state machine uses ticks.
    backoff_ticks: int


@dataclass(frozen=True)
class Panel:
    panel_id: str
    job_id: str

    priority: int
    sla_ms: int

    model_id: str
    affinity: Mapping[str, object]

    payload: Mapping[str, object]
    dedupe_key: str

    timeout_ticks: int
    retry: RetryPolicy


@dataclass(frozen=True)
class NodeCapability:
    node_id: str
    # Simplified capability vector (baseline RR in phase 1 may ignore most of this,
    # but eligibility constraints are still useful).
    tags: Mapping[str, object]


@dataclass(frozen=True)
class RunnerResult:
    # Payload is opaque to the runtime; caller interprets it.
    output: Mapping[str, object]


RunnerCallable = Callable[[Panel, NodeCapability], RunnerResult]


@dataclass(frozen=True)
class PanelExecutionRecord:
    state: PanelState
    attempt: int

    leased_by: str | None = None
    lease_expires_tick: int | None = None

    running_on: str | None = None
    started_tick: int | None = None

    succeeded_output: Mapping[str, object] | None = None
    failure_reason: str | None = None

    # Backoff scheduling
    next_eligible_tick: int = 0


@dataclass(frozen=True)
class PanelEvent:
    panel_id: str
    job_id: str
    event_type: str  # leased | dispatched | completed | failed | timed_out
    node_id: str | None
    attempt: int
    dedupe_key: str


@dataclass
class Phase1State:
    tick: int = 0

    # Panels lifecycle.
    panels: MutableMapping[str, PanelExecutionRecord] = field(default_factory=dict)

    # Job->panel ids for lifecycle grouping.
    job_panels: MutableMapping[str, list[str]] = field(default_factory=dict)

    # Dedupe success cache: dedupe_key -> output
    dedupe_success: MutableMapping[str, Mapping[str, object]] = field(
        default_factory=dict
    )

    # RR scheduling pointer among eligible nodes.
    rr_index: int = 0


def _stable_u64_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def pick_eligible_nodes(
    *,
    nodes: list[NodeCapability],
    panel: Panel,
) -> list[NodeCapability]:
    """
    Phase-1 eligibility (baseline):
    - Panel affinity keys act as a minimal constraint set.
    - Node tags must match all specified affinity keys using ==.
    - Unknown affinity keys in node tags => not eligible.
    """
    if not panel.affinity:
        return nodes

    eligible: list[NodeCapability] = []
    for n in nodes:
        ok = True
        for k, v in panel.affinity.items():
            if k not in n.tags:
                ok = False
                break
            if n.tags[k] != v:
                ok = False
                break
        if ok:
            eligible.append(n)
    return eligible


def rr_pick_next(
    *,
    eligible_nodes: list[NodeCapability],
    rr_index: int,
) -> tuple[NodeCapability, int]:
    if not eligible_nodes:
        raise ValueError("rr_pick_next requires non-empty eligible_nodes")
    idx = rr_index % len(eligible_nodes)
    return eligible_nodes[idx], idx + 1


def create_job_panels(
    *,
    job_id: str,
    panels: list[Panel],
) -> Phase1State:
    st = Phase1State()
    st.job_panels[job_id] = [p.panel_id for p in panels]
    for p in panels:
        st.panels[p.panel_id] = PanelExecutionRecord(
            state=PanelState.QUEUED,
            attempt=0,
            leased_by=None,
            lease_expires_tick=None,
            running_on=None,
            started_tick=None,
            succeeded_output=None,
            failure_reason=None,
            next_eligible_tick=0,
        )
    return st


@dataclass(frozen=True)
class TickInputs:
    # Runner function is injected by caller; runtime may call it synchronously
    # (caller decides whether runner internally does async work).
    runner: RunnerCallable
    nodes: list[NodeCapability]

    # Lease TTL expressed in ticks for determinism.
    lease_ttl_ticks: int


def step_phase1(  # noqa: C901
    *,
    state: Phase1State,
    panels: Mapping[str, Panel],
    inputs: TickInputs,
) -> tuple[Phase1State, list[PanelEvent]]:
    """
    Deterministic, single-tick transition:
    - advance queued->leased based on RR and eligibility
    - advance leased->running (dispatch)
    - invoke runner for running panels and determine completion
    - handle lease expiry (reclaim) and timeouts
    - enforce at-least-once with dedupe_key success caching

    Note:
    - This function mutates by returning a new Phase1State object with
      updated dict contents (records are replaced; Phase1State is a dataclass
      with mutable mappings).

    """
    now = state.tick
    out_events: list[PanelEvent] = []

    # Copy shallow structures (dict contents replaced).
    new_state = Phase1State(
        tick=state.tick + 1,
        panels=state.panels,
        job_panels=state.job_panels,
        dedupe_success=state.dedupe_success,
        rr_index=state.rr_index,
    )

    # 1) Reclaim expired leases for non-running panels.
    for panel_id, rec in list(new_state.panels.items()):
        if (
            rec.state == PanelState.LEASED
            and rec.lease_expires_tick is not None
            and now >= rec.lease_expires_tick
        ):
            panels[panel_id]
            new_state.panels[panel_id] = PanelExecutionRecord(
                state=PanelState.QUEUED,
                attempt=rec.attempt,
                leased_by=None,
                lease_expires_tick=None,
                running_on=None,
                started_tick=None,
                succeeded_output=None,
                failure_reason=None,
                next_eligible_tick=rec.next_eligible_tick,
            )
            # No event: lease expiry is internal reclaim in phase 1.

    # 2) Dispatch queued panels when eligible.
    # Phase-1 RR chooses one node per panel independently; we update rr_index as we go.
    for panel_id, rec in list(new_state.panels.items()):
        if rec.state != PanelState.QUEUED:
            continue

        if now < rec.next_eligible_tick:
            continue

        panel = panels[panel_id]

        # If dedupe already succeeded, mark as succeeded and archive later.
        if panel.dedupe_key in new_state.dedupe_success:
            out = new_state.dedupe_success[panel.dedupe_key]
            new_state.panels[panel_id] = PanelExecutionRecord(
                state=PanelState.SUCCEEDED,
                attempt=rec.attempt,
                leased_by=None,
                lease_expires_tick=None,
                running_on=None,
                started_tick=None,
                succeeded_output=out,
                failure_reason=None,
                next_eligible_tick=now,
            )
            out_events.append(
                PanelEvent(
                    panel_id=panel.panel_id,
                    job_id=panel.job_id,
                    event_type="completed",
                    node_id=None,
                    attempt=rec.attempt,
                    dedupe_key=panel.dedupe_key,
                )
            )
            continue

        eligible_nodes = pick_eligible_nodes(nodes=inputs.nodes, panel=panel)
        if not eligible_nodes:
            # No node can satisfy affinity constraints; remain queued.
            continue

        node, next_rr = rr_pick_next(
            eligible_nodes=eligible_nodes,
            rr_index=new_state.rr_index,
        )
        new_state.rr_index = next_rr

        lease_expires_tick = now + inputs.lease_ttl_ticks
        new_state.panels[panel_id] = PanelExecutionRecord(
            state=PanelState.LEASED,
            attempt=rec.attempt,
            leased_by=node.node_id,
            lease_expires_tick=lease_expires_tick,
            running_on=None,
            started_tick=None,
            succeeded_output=None,
            failure_reason=None,
            next_eligible_tick=rec.next_eligible_tick,
        )
        out_events.append(
            PanelEvent(
                panel_id=panel.panel_id,
                job_id=panel.job_id,
                event_type="leased",
                node_id=node.node_id,
                attempt=rec.attempt,
                dedupe_key=panel.dedupe_key,
            )
        )

    # 3) Advance leased->running and invoke runner for running panels.
    for panel_id, rec in list(new_state.panels.items()):
        if rec.state == PanelState.LEASED:
            if rec.leased_by is None:
                continue
            # Lease to running on next step (same tick for phase-1 simplicity).
            new_state.panels[panel_id] = PanelExecutionRecord(
                state=PanelState.RUNNING,
                attempt=rec.attempt,
                leased_by=rec.leased_by,
                lease_expires_tick=rec.lease_expires_tick,
                running_on=rec.leased_by,
                started_tick=now,
                succeeded_output=None,
                failure_reason=None,
                next_eligible_tick=rec.next_eligible_tick,
            )
            out_events.append(
                PanelEvent(
                    panel_id=panels[panel_id].panel_id,
                    job_id=panels[panel_id].job_id,
                    event_type="dispatched",
                    node_id=rec.leased_by,
                    attempt=rec.attempt,
                    dedupe_key=panels[panel_id].dedupe_key,
                )
            )
            continue

        if rec.state != PanelState.RUNNING:
            continue

        panel = panels[panel_id]
        if rec.running_on is None or rec.started_tick is None:
            continue

        # Timeout check.
        if rec.started_tick + panel.timeout_ticks <= now:
            if rec.attempt + 1 < panel.retry.max_attempts:
                # Retry: requeue with incremented attempt and backoff.
                next_attempt = rec.attempt + 1
                new_state.panels[panel_id] = PanelExecutionRecord(
                    state=PanelState.QUEUED,
                    attempt=next_attempt,
                    leased_by=None,
                    lease_expires_tick=None,
                    running_on=None,
                    started_tick=None,
                    succeeded_output=None,
                    failure_reason="timeout",
                    next_eligible_tick=now + panel.retry.backoff_ticks,
                )
            else:
                new_state.panels[panel_id] = PanelExecutionRecord(
                    state=PanelState.TIMED_OUT,
                    attempt=rec.attempt,
                    leased_by=None,
                    lease_expires_tick=None,
                    running_on=None,
                    started_tick=None,
                    succeeded_output=None,
                    failure_reason="timeout",
                    next_eligible_tick=now,
                )
            out_events.append(
                PanelEvent(
                    panel_id=panel.panel_id,
                    job_id=panel.job_id,
                    event_type="timed_out",
                    node_id=rec.running_on,
                    attempt=rec.attempt,
                    dedupe_key=panel.dedupe_key,
                )
            )
            continue

        # Runner invocation (deterministic from injected callable).
        node = next((n for n in inputs.nodes if n.node_id == rec.running_on), None)
        if node is None:
            # Node missing: treat as retryable failure.
            if rec.attempt + 1 < panel.retry.max_attempts:
                next_attempt = rec.attempt + 1
                new_state.panels[panel_id] = PanelExecutionRecord(
                    state=PanelState.QUEUED,
                    attempt=next_attempt,
                    leased_by=None,
                    lease_expires_tick=None,
                    running_on=None,
                    started_tick=None,
                    succeeded_output=None,
                    failure_reason="node_missing",
                    next_eligible_tick=now + panel.retry.backoff_ticks,
                )
            else:
                new_state.panels[panel_id] = PanelExecutionRecord(
                    state=PanelState.FAILED,
                    attempt=rec.attempt,
                    leased_by=None,
                    lease_expires_tick=None,
                    running_on=None,
                    started_tick=None,
                    succeeded_output=None,
                    failure_reason="node_missing",
                    next_eligible_tick=now,
                )
            out_events.append(
                PanelEvent(
                    panel_id=panel.panel_id,
                    job_id=panel.job_id,
                    event_type="failed",
                    node_id=rec.running_on,
                    attempt=rec.attempt,
                    dedupe_key=panel.dedupe_key,
                )
            )
            continue

        # Dedupe is allowed to short-circuit even for already-RUNNING panels:
        # if some other panel with the same dedupe_key already succeeded earlier
        # in this tick, we must not invoke the runner again.
        if panel.dedupe_key in new_state.dedupe_success:
            out = new_state.dedupe_success[panel.dedupe_key]
            new_state.panels[panel_id] = PanelExecutionRecord(
                state=PanelState.SUCCEEDED,
                attempt=rec.attempt,
                leased_by=None,
                lease_expires_tick=None,
                running_on=None,
                started_tick=None,
                succeeded_output=out,
                failure_reason=None,
                next_eligible_tick=now,
            )
            out_events.append(
                PanelEvent(
                    panel_id=panel.panel_id,
                    job_id=panel.job_id,
                    event_type="completed",
                    node_id=node.node_id,
                    attempt=rec.attempt,
                    dedupe_key=panel.dedupe_key,
                )
            )
            continue

        try:
            result = inputs.runner(panel, node)
        except Exception as exc:  # noqa: BLE001
            if rec.attempt + 1 < panel.retry.max_attempts:
                next_attempt = rec.attempt + 1
                new_state.panels[panel_id] = PanelExecutionRecord(
                    state=PanelState.QUEUED,
                    attempt=next_attempt,
                    leased_by=None,
                    lease_expires_tick=None,
                    running_on=None,
                    started_tick=None,
                    succeeded_output=None,
                    failure_reason=str(exc),
                    next_eligible_tick=now + panel.retry.backoff_ticks,
                )
            else:
                new_state.panels[panel_id] = PanelExecutionRecord(
                    state=PanelState.FAILED,
                    attempt=rec.attempt,
                    leased_by=None,
                    lease_expires_tick=None,
                    running_on=None,
                    started_tick=None,
                    succeeded_output=None,
                    failure_reason=str(exc),
                    next_eligible_tick=now,
                )
            out_events.append(
                PanelEvent(
                    panel_id=panel.panel_id,
                    job_id=panel.job_id,
                    event_type="failed",
                    node_id=node.node_id,
                    attempt=rec.attempt,
                    dedupe_key=panel.dedupe_key,
                )
            )
            continue

        # Success: dedupe by panel.dedupe_key.
        if panel.dedupe_key not in new_state.dedupe_success:
            new_state.dedupe_success[panel.dedupe_key] = dict(result.output)

        new_state.panels[panel_id] = PanelExecutionRecord(
            state=PanelState.SUCCEEDED,
            attempt=rec.attempt,
            leased_by=None,
            lease_expires_tick=None,
            running_on=None,
            started_tick=None,
            succeeded_output=new_state.dedupe_success[panel.dedupe_key],
            failure_reason=None,
            next_eligible_tick=now,
        )
        out_events.append(
            PanelEvent(
                panel_id=panel.panel_id,
                job_id=panel.job_id,
                event_type="completed",
                node_id=node.node_id,
                attempt=rec.attempt,
                dedupe_key=panel.dedupe_key,
            )
        )

    # 4) Archive succeeded/failed/timed_out panels deterministically (same tick).
    for panel_id, rec in list(new_state.panels.items()):
        if rec.state in {PanelState.SUCCEEDED, PanelState.FAILED, PanelState.TIMED_OUT}:
            new_state.panels[panel_id] = PanelExecutionRecord(
                state=PanelState.ARCHIVED,
                attempt=rec.attempt,
                leased_by=None,
                lease_expires_tick=None,
                running_on=None,
                started_tick=None,
                succeeded_output=rec.succeeded_output,
                failure_reason=rec.failure_reason,
                next_eligible_tick=rec.next_eligible_tick,
            )

    return new_state, out_events


def build_panel_key(panel_id: str, dedupe_key: str) -> str:
    """
    Helper for stable-ish panel identity checks in callers.
    Not required for correctness of state transitions.
    """
    return f"panel:{panel_id}:{_stable_u64_hex(dedupe_key)}"
