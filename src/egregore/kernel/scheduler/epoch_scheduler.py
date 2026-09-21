# epistemic marker: provenance / auditability
"""
EGREGORE LAW: Epoch Scheduler
Deterministic epoch-bound scheduling with TU allocation.
"""

from __future__ import annotations

from egregore.domain.work_unit import WorkUnit, WorkUnitState
from egregore.kernel.scheduler.tu_budget import EpochConfig, TUBudget


class EpochScheduler:
    def __init__(self, config: EpochConfig) -> None:
        self._config = config
        self._budget = TUBudget(config)
        self._epoch_number: int = 0
        self._epoch_start_ns: int = 0
        self._submitted: list[WorkUnit] = []
        self._admitted: list[WorkUnit] = []
        self._rejected: list[WorkUnit] = []

    def start_epoch(self, timestamp_ns: int) -> None:
        self._epoch_number += 1
        self._epoch_start_ns = timestamp_ns
        self._budget.start_epoch(timestamp_ns)
        self._submitted.clear()
        self._admitted.clear()
        self._rejected.clear()

    def submit(self, work_unit: WorkUnit, timestamp_ns: int) -> bool:
        self._submitted.append(work_unit)
        if self._budget.allocate(work_unit, timestamp_ns):
            admitted = work_unit.with_state(WorkUnitState.ADMITTED)
            self._admitted.append(admitted)
            return True
        else:
            rejected = work_unit.with_state(WorkUnitState.REJECTED)
            self._rejected.append(rejected)
            return False

    def end_epoch(self) -> dict:
        return {
            "epoch_number": self._epoch_number,
            "epoch_start_ns": self._epoch_start_ns,
            "budget_state": self._budget.to_canonical(),
            "submitted_count": len(self._submitted),
            "admitted_count": len(self._admitted),
            "rejected_count": len(self._rejected),
        }

    @property
    def epoch_number(self) -> int:
        return self._epoch_number

    @property
    def admitted(self) -> list[WorkUnit]:
        return list(self._admitted)

    @property
    def rejected(self) -> list[WorkUnit]:
        return list(self._rejected)
