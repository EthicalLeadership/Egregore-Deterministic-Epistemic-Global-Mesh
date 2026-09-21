"""
EGREGORE LAW: Powertrain Coupling
Links TU/DT budget to the gearbox for compute reservation.
"""

from __future__ import annotations

from egregore.domain.work_unit import WorkUnit
from egregore.kernel.scheduler.dt_monitor import DTMonitor, DTReading


class PowertrainCoupling:
    def __init__(self, dt_monitor: DTMonitor) -> None:
        self._dt_monitor = dt_monitor
        self._last_reading: DTReading | None = None

    def reserve_compute(self, work_unit: WorkUnit) -> bool:
        reading = self._dt_monitor.read()
        self._last_reading = reading
        return not work_unit.demand.dt > reading.dt_available

    def release_compute(self, work_unit: WorkUnit) -> None:
        pass

    @property
    def last_reading(self) -> DTReading | None:
        return self._last_reading

    def get_capacity_status(self) -> dict:
        reading = self._dt_monitor.read()
        self._last_reading = reading
        return {
            "dt_available": reading.dt_available.to_canonical(),
            "dt_total": reading.dt_total.to_canonical(),
            "thermal_throttle": reading.thermal_throttle,
            "source": reading.source,
        }
