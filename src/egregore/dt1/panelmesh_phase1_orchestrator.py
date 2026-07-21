from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from egregore.dt1.models import (
    CreditGrant,
    CreditRevoke,
    LaneKey,
    PressureSignal,
    WorkUnit,
)
from egregore.dt1.panelmesh_phase1_runtime import (
    NodeCapability,
    Panel,
    PanelEvent,
    PanelExecutionRecord,
    PanelState,
    Phase1State,
    RetryPolicy,
    RunnerResult,
    TickInputs,
    step_phase1,
)
from egregore.dt1.state_machines.credit_lease_sm import (
    CreditLease,
    LeaseState,
    LeaseStepInputs,
    step_credit_lease,
)
from egregore.dt1.state_machines.es_admission_sm import (
    EsAdmissionInputs,
    EsPressurePolicy,
    admit_workunit_es,
    resolve_es_pressure_level,
)


@dataclass(frozen=True)
class CreditLeaseUpdate:
    ttl_expired: bool
    grant: CreditGrant | None = None
    revoke: CreditRevoke | None = None


@dataclass(frozen=True)
class OrchestratorConfig:
    # Phase-1 panel lease TTL expressed in deterministic ticks.
    lease_ttl_ticks: int

    # Panel parameters derived by this orchestrator when converting WorkUnit -> Panel.
    panel_timeout_ticks: int = 10
    panel_retry_policy: RetryPolicy = field(
        default_factory=lambda: RetryPolicy(max_attempts=2, backoff_ticks=1)
    )
    panel_sla_ms: int = 120
    panel_model_id: str = "dt1-model"

    # ES admission knobs.
    need_wu: int = 1
    need_bytes: int = 0
    min_defer_slack_nanos: int = 0
    retry_after_ms: int = 0
    edge_can_execute: bool = True
    pressure_policy: EsPressurePolicy = field(default_factory=EsPressurePolicy)

    # Default initial lane credit lease.
    initial_credit_lease: CreditLease = field(
        default_factory=lambda: CreditLease(
            state=LeaseState.NO_CREDITS,
            credits_wu=0,
            credits_bytes=0,
            ttl_ms_remaining=0,
            epoch=0,
        )
    )

    # How to pick a stable deterministic runner output.
    # The orchestrator doesn't use runner output for correctness today,
    # but it must be present for panelmesh dedupe_success caching.
    runner_output_builder: Callable[[str, str], Mapping[str, object]] = (
        lambda panel_id, node_id: {
            "panel_id": panel_id,
            "node_id": node_id,
        }
    )


def _default_runner(
    panel: Panel,
    node: NodeCapability,
    *,
    output_builder: Callable[[str, str], Mapping[str, object]],
) -> RunnerResult:
    return RunnerResult(output=dict(output_builder(panel.panel_id, node.node_id)))


@dataclass
class OrchestratorState:
    phase1_state: Phase1State = field(default_factory=Phase1State)
    pending_workunits: dict[str, WorkUnit] = field(default_factory=dict)
    terminal_workunits: dict[str, str] = field(default_factory=dict)  # wu_id -> reason
    panels_by_id: dict[str, Panel] = field(default_factory=dict)  # panel_id -> Panel
    credit_leases: dict[LaneKey, CreditLease] = field(
        default_factory=dict
    )  # lane -> lease
    pressure_debounce: object = field(default_factory=lambda: None)  # set on first tick
    scheduled_workunit_ids: set[str] = field(
        default_factory=set
    )  # wu_id already converted to panels


class PanelmeshPhase1Orchestrator:
    """
    Deterministic orchestrator that ties together:
      - credit lease state machine (step_credit_lease)
      - pressure aggregation + hysteresis (resolve_es_pressure_level)
      - ES admission decision (admit_workunit_es)
      - panelmesh phase-1 scheduling/execution tick (step_phase1)

    This module is designed for replayability:
    - it does not perform IO
    - all nondeterminism is pushed into the injected RunnerCallable
      (default runner is deterministic).
    """

    def __init__(
        self,
        *,
        config: OrchestratorConfig,
        nodes: Sequence[NodeCapability],
        initial_workunits: Sequence[WorkUnit],
    ) -> None:
        self._config = config
        self._nodes = list(nodes)
        self._state = OrchestratorState(
            pending_workunits={self._wu_id(wu): wu for wu in initial_workunits},
        )

    @staticmethod
    def _wu_id(wu: WorkUnit) -> str:
        return f"wu:{wu.env.wu_id_hi}:{wu.env.wu_id_lo}"

    @staticmethod
    def lane_key_for_workunit(*, site: str, wu: WorkUnit) -> LaneKey:
        return LaneKey(
            dt1_class=wu.env.dt1_class,
            dt1_type=wu.env.dt1_type,
            priority=wu.env.priority,
            site=site,
        )

    def add_workunit(self, wu: WorkUnit) -> None:
        self._state.pending_workunits[self._wu_id(wu)] = wu

    @property
    def phase1_state(self) -> Phase1State:
        return self._state.phase1_state

    @property
    def pending_workunits(self) -> Mapping[str, WorkUnit]:
        return self._state.pending_workunits

    @property
    def terminal_workunits(self) -> Mapping[str, str]:
        return self._state.terminal_workunits

    def _get_or_init_lease(self, lane: LaneKey) -> CreditLease:
        existing = self._state.credit_leases.get(lane)
        if existing is not None:
            return existing
        self._state.credit_leases[lane] = self._config.initial_credit_lease
        return self._state.credit_leases[lane]

    def _ensure_pressure_debounce_initialized(self) -> None:
        # resolve_es_pressure_level expects a PressureDebounceState instance.
        # We rely on resolve_es_pressure_level to handle fields; but to avoid importing the type here,
        # we only store the returned next_state object.
        if self._state.pressure_debounce is None:
            # Use resolve_es_pressure_level's hysteresis implementation by calling once with empty signals.
            # But admit_workunit_es depends on effective pressure, so we need a real tick later anyway.
            # We initialize to a safe level=0 with an empty PressureDebounceState via the state machine module.
            from egregore.dt1.state_machines.pressure_aggregation_sm import (
                PressureDebounceState,
            )

            self._state.pressure_debounce = PressureDebounceState()

    def _panels_for_workunit(self, wu: WorkUnit) -> tuple[list[Panel], str]:
        """
        Convert a WorkUnit -> a list of Panels for phase-1.

        This orchestrator uses a conservative MVP mapping:
        - one panel per workunit
        - stable panel_id/job_id derived from wu_id
        """
        wu_id = self._wu_id(wu)
        panel_id = f"panel:{wu_id}"
        job_id = f"job:{wu_id}"

        # Affinity: no strict constraints by default in MVP.
        affinity: Mapping[str, object] = {}

        panel_priority: int = int(wu.env.priority)
        dedupe_key: str = (
            f"dedupe:{wu.env.dt1_class}.{wu.env.dt1_type}.{wu.env.priority}.{wu.env.wu_id_hi}:{wu.env.wu_id_lo}"
        )

        panel = Panel(
            panel_id=panel_id,
            job_id=job_id,
            priority=panel_priority,
            sla_ms=int(self._config.panel_sla_ms),
            model_id=str(self._config.panel_model_id),
            affinity=dict(affinity),
            payload={"wu_id": wu_id},
            dedupe_key=dedupe_key,
            timeout_ticks=int(self._config.panel_timeout_ticks),
            retry=self._config.panel_retry_policy,
        )
        return [panel], job_id

    def _schedule_workunit_if_needed(self, *, site: str, wu: WorkUnit) -> None:
        wu_id = self._wu_id(wu)
        if wu_id in self._state.scheduled_workunit_ids:
            return

        panels, job_id = self._panels_for_workunit(wu)

        # Initialize phase-1 records for panels.
        # We do NOT replace phase1_state.tick; we only ensure panel records exist.
        if job_id not in self._state.phase1_state.job_panels:
            self._state.phase1_state.job_panels[job_id] = []
        self._state.phase1_state.job_panels[job_id].extend([p.panel_id for p in panels])

        for p in panels:
            self._state.panels_by_id[p.panel_id] = p
            if p.panel_id not in self._state.phase1_state.panels:
                self._state.phase1_state.panels[p.panel_id] = PanelExecutionRecord(
                    state=PanelState.QUEUED,
                    attempt=0,
                    leased_by=None,
                    lease_expires_tick=None,
                    running_on=None,
                    started_tick=None,
                    succeeded_output=None,
                    failure_reason=None,
                    next_eligible_tick=self._state.phase1_state.tick,
                )

        self._state.scheduled_workunit_ids.add(wu_id)

    def tick(
        self,
        *,
        now_unix_nanos: int,
        pressure_signals: Sequence[PressureSignal],
        pressure_site: str,
        critical: bool,
        credit_lease_updates: Mapping[LaneKey, CreditLeaseUpdate] = {},
    ) -> list[PanelEvent]:
        """
        One deterministic orchestrator tick.

        credit_lease_updates:
          mapping from lane key -> update parameters for this tick.
          Lanes not present retain their previous lease state.
        """
        self._ensure_pressure_debounce_initialized()

        # 1) Credit lease update per lane.
        for lane, update in credit_lease_updates.items():
            prev = self._get_or_init_lease(lane)
            next_lease = step_credit_lease(
                prev,
                inputs=LeaseStepInputs(
                    ttl_expired=bool(update.ttl_expired),
                    grant=update.grant,
                    revoke=update.revoke,
                ),
            )
            self._state.credit_leases[lane] = next_lease

        # 2) Pressure aggregation + hysteresis => effective pressure level.
        from egregore.dt1.state_machines.pressure_aggregation_sm import (
            PressureDebounceState,
        )

        # mypy/typing: pressure_debounce is initialized above
        prev_debounce: PressureDebounceState = self._state.pressure_debounce  # type: ignore[assignment]  # compatibility: _ensure_pressure_debounce_initialized initializes this to PressureDebounceState
        effective_level, next_debounce, _aggregate = resolve_es_pressure_level(
            signals=pressure_signals,
            site=pressure_site,
            previous=prev_debounce,
            policy=self._config.pressure_policy,
        )
        self._state.pressure_debounce = next_debounce

        # 3) Admission for all pending workunits.
        pending_ids = list(self._state.pending_workunits.keys())
        for wu_id in pending_ids:
            wu = self._state.pending_workunits[wu_id]
            lane = self.lane_key_for_workunit(site=pressure_site, wu=wu)

            lease = self._get_or_init_lease(lane)

            es_inputs = EsAdmissionInputs(
                now_unix_nanos=int(now_unix_nanos),
                pressure_level=int(effective_level),
                critical=bool(critical),
                credits_wu=int(lease.credits_wu),
                credits_bytes=int(lease.credits_bytes),
                credits_ttl_ms_remaining=int(lease.ttl_ms_remaining),
                credits_epoch=int(lease.epoch),
                need_wu=int(self._config.need_wu),
                need_bytes=int(self._config.need_bytes),
                min_defer_slack_nanos=int(self._config.min_defer_slack_nanos),
                retry_after_ms=int(self._config.retry_after_ms),
                edge_can_execute=bool(self._config.edge_can_execute),
            )

            outcome = admit_workunit_es(wu=wu, lane=lane, inputs=es_inputs)

            if outcome.decision == "DEFERRED":
                continue

            if outcome.decision == "REJECTED":
                self._state.terminal_workunits[wu_id] = outcome.reason
                del self._state.pending_workunits[wu_id]
                continue

            if outcome.decision in {"ACCEPTED", "PUBLISHED"}:
                self._schedule_workunit_if_needed(site=pressure_site, wu=wu)
                # For MVP: once scheduled, remove from pending.
                del self._state.pending_workunits[wu_id]
                continue

            # Fail-closed: unknown decision string.
            self._state.terminal_workunits[wu_id] = outcome.reason
            del self._state.pending_workunits[wu_id]

        # 4) Run one panelmesh phase-1 tick.
        def runner(panel, node):
            return RunnerResult(
                output=dict(
                    self._config.runner_output_builder(panel.panel_id, node.node_id)
                )
            )

        tick_inputs = TickInputs(
            runner=runner,
            nodes=self._nodes,
            lease_ttl_ticks=int(self._config.lease_ttl_ticks),
        )

        self._state.phase1_state, events = step_phase1(
            state=self._state.phase1_state,
            panels=self._state.panels_by_id,
            inputs=tick_inputs,
        )
        return events
