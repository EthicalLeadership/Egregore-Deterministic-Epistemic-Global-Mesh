from __future__ import annotations

from typing import Protocol

from egregore.domain.gearbox_state import GearboxState, GearboxTransition


class IGearboxPolicy(Protocol):
    def decide(
        self,
        *,
        state: GearboxState,
        temp_c: float,
        vram_pct: float,
        depth: int,
        now_s: float,
    ) -> GearboxTransition: ...
