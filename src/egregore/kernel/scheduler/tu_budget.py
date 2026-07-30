# epistemic marker: provenance / auditability
"""
EGREGORE LAW: TU Budget
Epoch-bound allocation of Temporal Units.
"""

from __future__ import annotations

from dataclasses import dataclass

from egregore.domain.units import TU
from egregore.domain.work_unit import WorkUnit


@dataclass(frozen=True, slots=True)
class EpochConfig:
    duration_ms: int = 1000
    tu_budget: TU = TU(100)
    max_backlog: int = 200


class TUBudget:
    def __init__(self, config: EpochConfig) -> None:
        self._config = config
        self._allocated: dict[str, TU] = {}
        self._epoch_start_ns: int = 0
        self._allocated_count: int = 0
        self._rejected_count: int = 0

    def start_epoch(self, timestamp_ns: int) -> None:
        self._epoch_start_ns = timestamp_ns
        self._allocated.clear()
        self._allocated_count = 0
        self._rejected_count = 0

    def allocate(self, work_unit: WorkUnit, timestamp_ns: int) -> bool:
        if work_unit.work_unit_id in self._allocated:
            return False
        if self._allocated_count >= self._config.max_backlog:
            return False
        remaining = self.remaining
        if work_unit.demand.tu > remaining:
            self._rejected_count += 1
            return False
        self._allocated[work_unit.work_unit_id] = work_unit.demand.tu
        self._allocated_count += 1
        return True

    def release(self, work_unit_id: str) -> None:
        if work_unit_id in self._allocated:
            del self._allocated[work_unit_id]
            self._allocated_count -= 1

    @property
    def remaining(self) -> TU:
        used = sum(tu.value for tu in self._allocated.values())
        return TU(
            self._config.tu_budget.value - used, self._config.tu_budget.tau_max_ns
        )

    @property
    def allocated_count(self) -> int:
        return self._allocated_count

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    def to_canonical(self) -> dict:
        return {
            "epoch_duration_ms": self._config.duration_ms,
            "tu_budget": self._config.tu_budget.to_canonical(),
            "tu_allocated": TU(
                sum(tu.value for tu in self._allocated.values())
            ).to_canonical(),
            "remaining_tu": self.remaining.to_canonical(),
            "allocated_count": self._allocated_count,
            "rejected_count": self._rejected_count,
        }
