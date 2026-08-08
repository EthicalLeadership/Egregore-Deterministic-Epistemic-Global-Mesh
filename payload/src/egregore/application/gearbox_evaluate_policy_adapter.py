from __future__ import annotations

from egregore.domain.gearbox_state import GearboxTransition
from egregore.interface.gearbox_port import IGearboxPolicy
from egregore.powertrain.gearbox import Gearbox


class GearboxEvaluatePolicyAdapter(IGearboxPolicy):
    """
    Application adapter: exposes the stateful compatibility `Gearbox` as a pure
    IGearboxPolicy-like port for `ThermalGovernorService`.

    - The adapter delegates to `Gearbox.evaluate()` (preserving existing semantics).
    - It returns the updated domain state snapshot owned by `Gearbox`.
    """

    def __init__(self, *, gearbox: Gearbox) -> None:
        self._gearbox = gearbox

    def decide(
        self,
        *,
        state,  # state is carried by the caller/service; adapter delegates to gearbox's internal state
        temp_c: float,
        vram_pct: float,
        depth: int,
        now_s: float,
    ) -> GearboxTransition:
        before = self._gearbox.gear
        self._gearbox.evaluate(
            temp_c=temp_c, vram_pct=vram_pct, depth=depth, now_s=now_s
        )
        after = self._gearbox.gear

        # `Gearbox` provides a domain state snapshot.
        next_state = self._gearbox.domain_state()
        shifted = before != after
        return GearboxTransition(next_state=next_state, shifted=shifted)
