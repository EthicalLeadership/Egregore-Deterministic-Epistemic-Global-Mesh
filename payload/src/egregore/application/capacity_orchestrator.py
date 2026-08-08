"""EGREGORE LAW: Capacity Orchestrator."""

from __future__ import annotations

from egregore.application.admission_controller import (
    AdmissionController,
    AdmissionDecision,
    CapacityBudget,
)
from egregore.application.placement_policy import decide_placement
from egregore.application.pressure_controller import PressureController
from egregore.domain.units import DT, TU
from egregore.domain.work_unit import (
    WorkUnit,
    WorkUnitDemand,
    WorkUnitType,
    create_work_unit,
)
from egregore.domain.work_unit_defaults import register_all_defaults
from egregore.infrastructure.hardware_profiler import hardware_snapshot
from egregore.infrastructure.metrics.tu_metrics import EpochMetrics, TUMetricsCollector
from egregore.interface.model_host_ports import IModelHost, InferenceRequest
from egregore.interface.placement_policy_ports import PlacementDecision
from egregore.kernel.scheduler.dt_monitor import DTMonitor, StaticDTMonitor
from egregore.kernel.scheduler.epoch_scheduler import EpochConfig, EpochScheduler


class CapacityOrchestrator:
    def __init__(
        self,
        admission_controller: AdmissionController,
        epoch_scheduler: EpochScheduler,
        dt_monitor: DTMonitor,
        model_host: IModelHost | None = None,
    ) -> None:
        self._admission = admission_controller
        self._scheduler = epoch_scheduler
        self._dt_monitor = dt_monitor
        self._model_host = model_host
        self._pressure = PressureController()
        self._metrics = TUMetricsCollector()
        self._epoch_count = 0

    @classmethod
    def build_default(
        cls,
        total_dt: DT | None = None,
        total_tu: TU | None = None,
        epoch_duration_ms: int = 1000,
    ) -> CapacityOrchestrator:
        register_all_defaults()
        total_dt = total_dt if total_dt is not None else DT(9.0)
        total_tu = total_tu if total_tu is not None else TU(100)
        budget = CapacityBudget(total_dt=total_dt, total_tu=total_tu)
        admission = AdmissionController(budget)
        scheduler = EpochScheduler(
            EpochConfig(duration_ms=epoch_duration_ms, tu_budget=total_tu)
        )
        dt_monitor = StaticDTMonitor(total_dt)
        return cls(admission, scheduler, dt_monitor, None)

    def start_epoch(self, timestamp_ns: int) -> None:
        self._epoch_count += 1
        self._scheduler.start_epoch(timestamp_ns)

    def submit_work_unit(self, work_unit: WorkUnit) -> AdmissionDecision:
        result = self._admission.evaluate(work_unit)
        if (
            result.decision == AdmissionDecision.ADMITTED
            and not self._scheduler.submit(result.work_unit, result.timestamp_ns)
        ):
            return AdmissionDecision.REJECTED_BACKLOG_EXCEEDED
        return result.decision

    def submit_inference(self, request: InferenceRequest) -> AdmissionDecision:
        if self._model_host is None:
            raise RuntimeError("No model host configured")
        demand = self._model_host.get_demand_profile(request)
        work_unit = create_work_unit(
            work_unit_type=WorkUnitType.LLM_INFERENCE,
            demand=demand,
            payload=request.input_data,
            metadata={"model_id": request.model_id, "backend": request.backend},
        )
        return self.submit_work_unit(work_unit)

    def schedule_inference(
        self, request: InferenceRequest, model_size_bytes: int
    ) -> tuple[AdmissionDecision, PlacementDecision, str | None]:
        """
        Decide placement, schedule, and return (decision, placement, work_unit_id).

        The orchestrator profiles hardware, chooses CPU vs GPU, admits the work
        unit, and returns the placement decision for the model host to apply.
        """
        if self._model_host is None:
            raise RuntimeError("No model host configured")

        hw = hardware_snapshot()
        placement = decide_placement(model_size_bytes=model_size_bytes, hardware=hw)

        # Adjust demand: GPU inference is cheaper in TU (faster) but still costs DT.
        base_demand = self._model_host.get_demand_profile(request)
        dt = base_demand.dt
        tu = base_demand.tu
        if placement.n_gpu_layers != 0:
            tu = TU(max(1, int(tu.value * 0.5)))
        else:
            dt = dt * 1.2

        work_unit = create_work_unit(
            work_unit_type=WorkUnitType.LLM_INFERENCE,
            demand=WorkUnitDemand(
                dt=dt,
                tu=tu,
                priority=base_demand.priority,
                max_wait_ms=base_demand.max_wait_ms,
            ),
            payload=request.input_data,
            metadata={
                "model_id": request.model_id,
                "backend": request.backend,
                "placement": {
                    "n_gpu_layers": placement.n_gpu_layers,
                    "reason": placement.reason,
                },
            },
        )

        decision = self.submit_work_unit(work_unit)
        work_unit_id = (
            work_unit.work_unit_id if decision == AdmissionDecision.ADMITTED else None
        )
        return decision, placement, work_unit_id

    def end_epoch(self, timestamp_ns: int) -> dict:
        log = self._scheduler.end_epoch()
        metrics = EpochMetrics(
            epoch_number=self._epoch_count,
            duration_ms=log.get("budget_state", {}).get("epoch_duration_ms", 1000),
            work_units_submitted=log.get("submitted_count", 0),
            work_units_admitted=log.get("admitted_count", 0),
            work_units_rejected=log.get("rejected_count", 0),
            tu_allocated=log.get("budget_state", {})
            .get("tu_allocated", {})
            .get("value", 0),
            tu_remaining=log.get("budget_state", {})
            .get("remaining_tu", {})
            .get("value", 0),
            dt_consumed=sum(w.demand.dt.value for w in self._scheduler.admitted),
        )
        self._metrics.record_epoch(metrics)
        return log

    def get_pressure_recommendation(self) -> int:
        if not self._metrics.epochs:
            return 0
        latest = self._metrics.epochs[-1]
        total_tu = self._admission._budget._total_tu.value
        max_backlog = self._admission._budget._max_backlog
        return self._pressure.evaluate(latest, total_tu, max_backlog)

    def get_pressure_trend(self) -> dict:
        return self._pressure.get_trend()

    def get_metrics_report(self) -> dict:
        return self._metrics.generate_report()

    def get_capacity_status(self) -> dict:
        return {
            "available_dt": self._admission._budget.available_dt.to_canonical(),
            "available_tu": self._admission._budget.available_tu.to_canonical(),
            "backlog_count": self._admission._budget.backlog_count,
            "epoch_count": self._epoch_count,
        }
