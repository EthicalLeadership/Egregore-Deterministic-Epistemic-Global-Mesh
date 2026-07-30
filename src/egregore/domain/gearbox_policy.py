# epistemic marker: provenance / auditability
from __future__ import annotations

from dataclasses import dataclass

from egregore.domain.gearbox_config import GearboxConfig
from egregore.domain.gearbox_state import Gear, GearboxState, GearboxTransition


@dataclass(frozen=True)
class GearboxPolicy:
    """
    Pure gearbox decision policy.

    This class is stateless: all state is carried by the GearboxState input/output.
    """

    config: GearboxConfig

    def decide(
        self,
        *,
        state: GearboxState,
        temp_c: float,
        vram_pct: float,
        depth: int,
        now_s: float,
    ) -> GearboxTransition:
        t = float(temp_c)
        v = float(vram_pct)
        d = int(depth)
        n = float(now_s)

        # Emergency upshift to G5 if any trigger hits.
        if t >= 83.0 or v >= 95.0 or d >= self.config.q_block:
            if state.gear != Gear.G5:
                return GearboxTransition(
                    next_state=GearboxState(gear=Gear.G5, last_shift_s=n),
                    shifted=True,
                )
            # Stay in G5: cooldown reference must not be reset.
            return GearboxTransition(next_state=state, shifted=False)

        # Hysteresis when already in G5.
        if (
            state.gear == Gear.G5
            and t < 78.0
            and d < self.config.q_high
            and n - state.last_shift_s > self.config.g5_to_g2_cooldown_s
        ):
            return GearboxTransition(
                next_state=GearboxState(gear=Gear.G2, last_shift_s=n),
                shifted=True,
            )
        if state.gear == Gear.G5:
            return GearboxTransition(next_state=state, shifted=False)

        # Normal shifts when not in G5.
        if d > self.config.q_high:
            if state.gear == Gear.G2:
                return GearboxTransition(next_state=state, shifted=False)
            return GearboxTransition(
                next_state=GearboxState(gear=Gear.G2, last_shift_s=n),
                shifted=True,
            )

        if d == 0 and t < 40.0:
            if state.gear == Gear.G0:
                return GearboxTransition(next_state=state, shifted=False)
            return GearboxTransition(
                next_state=GearboxState(gear=Gear.G0, last_shift_s=n),
                shifted=True,
            )

        return GearboxTransition(next_state=state, shifted=False)
