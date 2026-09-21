from __future__ import annotations

from collections.abc import Iterable

from egregore.domain.gearbox_state import GearboxState
from egregore.domain.provenance_model import ProvenanceEvent
from egregore.interface.gearbox_port import IGearboxPolicy
from egregore.interface.provenance_port import IProvenanceSink
from egregore.interface.thermal_types import ThermalSample


class ThermalGovernorService:
    """
    Application orchestration for thermal governance.

    Responsibilities:
    - iterate samples
    - maintain gearbox state transitions via IGearboxPolicy
    - emit provenance events via IProvenanceSink when gear == G5
    - no direct persistence / no disk / no network calls
    """

    def __init__(
        self,
        *,
        gearbox_policy: IGearboxPolicy,
        initial_state: GearboxState,
        provenance_sink: IProvenanceSink,
    ) -> None:
        self._gearbox_policy = gearbox_policy
        self._state = initial_state
        self._provenance_sink = provenance_sink

    def process(self, samples: Iterable[ThermalSample]) -> int:
        emitted = 0
        for s in samples:
            transition = self._gearbox_policy.decide(
                state=self._state,
                temp_c=s.temp_c,
                vram_pct=s.vram_pct,
                depth=s.depth,
                now_s=s.now_s,
            )
            self._state = transition.next_state

            if self._state.gear.name == "G5" or int(self._state.gear) == 5:
                # Deterministic provenance timestamp: NO wall-clock.
                # Derive from the deterministic command-like inputs (sample + resulting gear).
                import hashlib

                raw = (
                    f"{s.temp_c}|{s.vram_pct}|{s.depth}|{s.now_s}|{int(self._state.gear)}"
                ).encode()
                digest = hashlib.sha256(raw).digest()
                ts_ns = int.from_bytes(digest[:8], byteorder="big")

                self._provenance_sink.append(
                    ProvenanceEvent(
                        engine="thermal",
                        event="PRESSURE_ENERGY",
                        payload={
                            "temp_c": s.temp_c,
                            "vram_pct": s.vram_pct,
                            "depth": s.depth,
                            "gear": int(self._state.gear),
                        },
                        ts_ns=ts_ns,
                    )
                )
                emitted += 1
        return emitted

    @property
    def state(self) -> GearboxState:
        return self._state
