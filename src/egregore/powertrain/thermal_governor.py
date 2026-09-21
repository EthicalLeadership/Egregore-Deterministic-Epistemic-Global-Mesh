from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from egregore.application.gearbox_evaluate_policy_adapter import (
    GearboxEvaluatePolicyAdapter,
)
from egregore.application.thermal_governor_service import ThermalGovernorService
from egregore.infrastructure.zarc_provenance_sink import ZarcProvenanceSink
from egregore.kernel.provenance import Provenance
from egregore.powertrain.gearbox import Gearbox


@dataclass(frozen=True)
class ThermalSample:
    temp_c: float
    vram_pct: float
    depth: int
    now_s: float


class ThermalGovernorTestMode:
    """
    CPU-only deterministic “thermal governor” that evaluates gearbox and emits `.zarc` entries.

    Enforceable architecture update:
    - orchestration lives in `ThermalGovernorService` (application layer)
    - persistence side effects are isolated behind `IProvenanceSink` (via `ZarcProvenanceSink`)
    - this class remains a compatibility wrapper for existing unit tests
    """

    def __init__(self, *, gearbox: Gearbox, provenance: Provenance) -> None:
        self._gearbox = gearbox
        self._provenance = provenance

    def run(self, samples: Iterable[ThermalSample]) -> int:
        sink = ZarcProvenanceSink(provenance=self._provenance)
        policy = GearboxEvaluatePolicyAdapter(gearbox=self._gearbox)

        service = ThermalGovernorService(
            gearbox_policy=policy,
            initial_state=self._gearbox.domain_state(),
            provenance_sink=sink,
        )
        return service.process(samples)
