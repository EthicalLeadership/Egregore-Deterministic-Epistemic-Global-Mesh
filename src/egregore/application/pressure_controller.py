"""BLACKSTAR LAW: Pressure Controller
Feedback loop that adjusts lane count based on epoch metrics.
"""

from __future__ import annotations

from dataclasses import dataclass

from egregore.infrastructure.metrics.tu_metrics import EpochMetrics


@dataclass(frozen=True, slots=True)
class PressureConfig:
    tu_util_target_low: float = 0.75
    tu_util_target_high: float = 0.90
    backlog_ratio_threshold: float = 0.20
    idle_ratio_threshold: float = 0.05
    max_lane_delta_per_epoch: int = 1


class PressureController:
    def __init__(self, config: PressureConfig | None = None) -> None:
        self._config = config or PressureConfig()
        self._last_recommendation: int = 0
        self._history: list = []

    def evaluate(self, metrics: EpochMetrics, total_tu: int, max_backlog: int) -> int:
        tu_util = metrics.tu_allocated / total_tu if total_tu > 0 else 0.0
        backlog_ratio = (
            metrics.work_units_submitted / max_backlog if max_backlog > 0 else 0.0
        )

        self._history.append({"tu_util": tu_util, "backlog_ratio": backlog_ratio})

        # Too idle and some backlog → shrink
        if tu_util < 0.70 and backlog_ratio > self._config.idle_ratio_threshold:
            rec = -1
        # Overloaded or high backlog → expand
        elif (
            tu_util > self._config.tu_util_target_high
            or backlog_ratio > self._config.backlog_ratio_threshold
        ):
            rec = +1
        # Just right → hold
        else:
            rec = 0

        # Rate limit
        if abs(rec) > self._config.max_lane_delta_per_epoch:
            rec = (
                self._config.max_lane_delta_per_epoch
                if rec > 0
                else -self._config.max_lane_delta_per_epoch
            )

        self._last_recommendation = rec
        return rec

    @property
    def last_recommendation(self) -> int:
        return self._last_recommendation

    def get_trend(self) -> dict:
        if not self._history:
            return {"status": "NO_DATA"}
        avg_util = sum(h["tu_util"] for h in self._history) / len(self._history)
        avg_backlog = sum(h["backlog_ratio"] for h in self._history) / len(
            self._history
        )
        return {
            "status": "OK",
            "epochs_tracked": len(self._history),
            "avg_tu_util": avg_util,
            "avg_backlog_ratio": avg_backlog,
            "last_recommendation": self._last_recommendation,
        }
