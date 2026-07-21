from __future__ import annotations

from typing import Generic, Protocol, TypeVar

from egregore.domain.hardware_work_unit import DTProfile, GearMode, TurbineUnit

T = TypeVar("T")


class ITransitLayer(Protocol, Generic[T]):
    """
    Transit Layer port (local mechanical buffer).

    Runner/app code must depend on this interface, not the concrete
    infrastructure implementation, to satisfy dependency rules.
    """

    def put(
        self, item: T, *, block: bool = True, timeout_sec: float | None = None
    ) -> None: ...
    def get(self, *, timeout_sec: float | None = None) -> T | None: ...
    def close(self) -> None: ...


class IHardwareProbe(Protocol):
    def probe(self) -> dict[str, float]: ...


class ITurbineUnit(Protocol):
    tu_id: str


class ISchedulerControl(Protocol):
    def allocate(
        self, *, dt_profile: DTProfile
    ) -> tuple[TurbineUnit, dict[str, object], GearMode]: ...


class IDeterministicThroughput(Protocol):
    def measure(self, *, jobs_processed: int, elapsed_s: float) -> float: ...
