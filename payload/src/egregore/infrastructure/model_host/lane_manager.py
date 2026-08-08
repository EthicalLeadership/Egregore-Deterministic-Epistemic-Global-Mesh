"""EGREGORE LAW: Lane Manager
Dynamic worker pool for model inference. Plane 2 only.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from egregore.domain.units import DT, TU
from egregore.domain.work_unit import WorkUnitDemand
from egregore.interface.model_host_ports import (
    IModelHost,
    InferenceRequest,
    InferenceResult,
)


@dataclass(frozen=True, slots=True)
class LaneState:
    lane_id: int
    active: bool
    current_work_unit_id: str | None
    dt_reserved: DT
    tu_reserved: TU


class LaneManager:
    def __init__(
        self, model_host: IModelHost, min_lanes: int = 1, max_lanes: int = 8
    ) -> None:
        self._host = model_host
        self._min_lanes = min_lanes
        self._max_lanes = max_lanes
        self._current_lanes = min_lanes
        self._executor: ThreadPoolExecutor | None = None
        self._lanes: list[LaneState] = []
        self._resize(self._min_lanes)

    def _resize(self, new_size: int) -> None:
        clamped = max(self._min_lanes, min(self._max_lanes, new_size))
        if clamped == self._current_lanes and self._executor is not None:
            return
        if self._executor:
            self._executor.shutdown(wait=False)
        self._executor = ThreadPoolExecutor(max_workers=clamped)
        self._current_lanes = clamped
        self._lanes = [
            LaneState(
                lane_id=i,
                active=True,
                current_work_unit_id=None,
                dt_reserved=DT(0),
                tu_reserved=TU(0),
            )
            for i in range(clamped)
        ]

    def resize(self, delta: int) -> int:
        new_size = self._current_lanes + delta
        self._resize(new_size)
        return self._current_lanes

    def get_lane_count(self) -> int:
        return self._current_lanes

    def submit(self, request: InferenceRequest) -> InferenceResult:
        if not self._host.is_available():
            raise RuntimeError("Model host unavailable")
        return self._host.generate(request)

    def get_capacity_per_lane(self) -> WorkUnitDemand:
        return self._host.get_demand_profile(
            InferenceRequest(model_id="default", input_data=b"")
        )

    def get_status(self) -> dict:
        return {
            "lane_count": self._current_lanes,
            "min_lanes": self._min_lanes,
            "max_lanes": self._max_lanes,
            "host_available": self._host.is_available(),
            "lanes": [lane.lane_id for lane in self._lanes],
        }

    def shutdown(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=True)
