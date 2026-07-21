"""
BLACKSTAR LAW: Admission Controller
Fail-closed gatekeeper. Minimize waiting, not maximize utilization.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from egregore.domain.units import DT, DT_ZERO, TU, TU_ZERO
from egregore.domain.work_unit import WorkUnit, WorkUnitRegistry


class AdmissionDecision(Enum):
    ADMITTED = auto()
    REJECTED_DT_INSUFFICIENT = auto()
    REJECTED_TU_INSUFFICIENT = auto()
    REJECTED_WAIT_EXCEEDS_THRESHOLD = auto()
    REJECTED_BACKLOG_EXCEEDED = auto()


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    decision: AdmissionDecision
    work_unit: WorkUnit
    reason: str
    dt_reserved: DT
    tu_allocated: TU
    timestamp_ns: int


class CapacityBudget:
    def __init__(
        self, total_dt: DT, total_tu: TU, headroom_factor: float = 0.9
    ) -> None:
        self._total_dt = total_dt
        self._total_tu = total_tu
        self._headroom_factor = headroom_factor
        self._available_dt = total_dt * headroom_factor
        self._available_tu = total_tu

    @property
    def available_dt(self) -> DT:
        return self._available_dt

    @property
    def available_tu(self) -> TU:
        return self._available_tu

    def allocate(self, dt: DT, tu: TU) -> None:
        self._available_dt -= dt
        self._available_tu -= tu

    def release(self, dt: DT, tu: TU) -> None:
        self._available_dt += dt
        self._available_tu += tu

    def can_allocate(self, dt: DT, tu: TU) -> bool:
        return self._available_dt >= dt and self._available_tu >= tu

    @property
    def backlog_count(self) -> int:
        # TODO: migrate to AdmissionController.backlog_count; budget should not track backlog
        return 0


class AdmissionController:
    def __init__(
        self,
        budget: CapacityBudget,
        max_backlog: int = 35,
        backlog_store: Any | None = None,
    ) -> None:
        self._budget = budget
        self._max_backlog = max_backlog
        self._backlog_store = backlog_store
        self._backlog: list[WorkUnit] = []

    def _backlog_count(self) -> int:
        if self._backlog_store is not None:
            return len(self._backlog_store.list_units())
        return len(self._backlog)

    def _append_backlog(self, work_unit: WorkUnit) -> None:
        if self._backlog_store is not None:
            self._backlog_store.append(work_unit)
        self._backlog.append(work_unit)

    def _remove_backlog(self, work_unit: WorkUnit) -> None:
        if self._backlog_store is not None:
            self._backlog_store.remove(work_unit)
        if work_unit in self._backlog:
            self._backlog.remove(work_unit)

    def evaluate(self, work_unit: WorkUnit) -> AdmissionResult:
        if self._backlog_count() >= self._max_backlog:
            return AdmissionResult(
                decision=AdmissionDecision.REJECTED_WAIT_EXCEEDS_THRESHOLD,
                work_unit=work_unit,
                reason=f"Wait backlog exceeds threshold ({self._max_backlog})",
                dt_reserved=DT_ZERO,
                tu_allocated=TU_ZERO,
                timestamp_ns=0,
            )

        if not WorkUnitRegistry.is_registered(work_unit.work_unit_type):
            return AdmissionResult(
                decision=AdmissionDecision.REJECTED_BACKLOG_EXCEEDED,
                work_unit=work_unit,
                reason="Work unit type not registered",
                dt_reserved=DT_ZERO,
                tu_allocated=TU_ZERO,
                timestamp_ns=0,
            )
        demand = work_unit.demand

        if not self._budget.can_allocate(demand.dt, demand.tu):
            if demand.dt > self._budget.available_dt:
                return AdmissionResult(
                    decision=AdmissionDecision.REJECTED_DT_INSUFFICIENT,
                    work_unit=work_unit,
                    reason="DT budget insufficient",
                    dt_reserved=DT_ZERO,
                    tu_allocated=TU_ZERO,
                    timestamp_ns=0,
                )
            else:
                return AdmissionResult(
                    decision=AdmissionDecision.REJECTED_TU_INSUFFICIENT,
                    work_unit=work_unit,
                    reason="TU budget insufficient",
                    dt_reserved=DT_ZERO,
                    tu_allocated=TU_ZERO,
                    timestamp_ns=0,
                )

        self._budget.allocate(demand.dt, demand.tu)
        self._append_backlog(work_unit)
        return AdmissionResult(
            decision=AdmissionDecision.ADMITTED,
            work_unit=work_unit,
            reason="Admitted",
            dt_reserved=demand.dt,
            tu_allocated=demand.tu,
            timestamp_ns=0,
        )

    def release(self, work_unit: WorkUnit) -> None:
        if work_unit in self._backlog or self._backlog_store is not None:
            self._budget.release(work_unit.demand.dt, work_unit.demand.tu)
            self._remove_backlog(work_unit)
