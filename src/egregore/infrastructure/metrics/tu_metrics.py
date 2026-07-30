"""EGREGORE LAW: TU Metrics. Plane 2 only."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EpochMetrics:
    epoch_number: int
    duration_ms: int
    work_units_submitted: int
    work_units_admitted: int
    work_units_rejected: int
    tu_allocated: int
    tu_remaining: int
    dt_consumed: float


class TUMetricsCollector:
    def __init__(self) -> None:
        self._epochs: list[EpochMetrics] = []
        self._total_admitted: int = 0
        self._total_rejected: int = 0

    def record_epoch(self, metrics: EpochMetrics) -> None:
        self._epochs.append(metrics)
        self._total_admitted += metrics.work_units_admitted
        self._total_rejected += metrics.work_units_rejected

    def generate_report(self) -> dict:
        if not self._epochs:
            return {"status": "NO_DATA", "total_epochs": 0}
        total = len(self._epochs)
        admitted = sum(e.work_units_admitted for e in self._epochs)
        rejected = sum(e.work_units_rejected for e in self._epochs)
        avg_latency = sum(e.duration_ms for e in self._epochs) / total
        return {
            "status": "OK",
            "total_epochs": total,
            "total_admitted": admitted,
            "total_rejected": rejected,
            "admission_rate": (
                admitted / (admitted + rejected) if (admitted + rejected) > 0 else 0.0
            ),
            "avg_epoch_duration_ms": avg_latency,
            "last_epoch": self._epochs[-1].epoch_number if self._epochs else 0,
        }

    @property
    def epochs(self) -> list[EpochMetrics]:
        return list(self._epochs)
