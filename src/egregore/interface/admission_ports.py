"""
BLACKSTAR LAW: Admission Ports
Injection-friendly port definitions for capacity and admission.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from egregore.domain.units import DT, TU
from egregore.domain.work_unit import WorkUnit


@runtime_checkable
class ICapacityProvider(Protocol):
    """Port for providing current system capacity (DT and TU)."""

    def get_available_dt(self) -> DT: ...

    def get_available_tu(self) -> TU: ...

    def get_total_dt(self) -> DT: ...

    def get_total_tu(self) -> TU: ...


@runtime_checkable
class IAdmissionLogger(Protocol):
    """Port for logging admission decisions to provenance."""

    def log_admission(
        self, work_unit: WorkUnit, admitted: bool, reason: str, timestamp_ns: int
    ) -> None: ...
