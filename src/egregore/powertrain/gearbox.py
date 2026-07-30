# epistemic marker: provenance / auditability
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from egregore.domain.gearbox_config import GearboxConfig as DomainGearboxConfig
from egregore.domain.gearbox_policy import GearboxPolicy
from egregore.domain.gearbox_state import (
    Gear as DomainGear,
)
from egregore.domain.gearbox_state import (
    GearboxState as DomainGearboxState,
)


class Gear(IntEnum):
    G0 = 0
    G2 = 2
    G5 = 5


@dataclass(frozen=True)
class GearboxConfig:
    q_high: int = 100
    q_block: int = 500
    g5_to_g2_cooldown_s: float = 30.0


class Gearbox:
    """
    Deterministic gearbox policy with hysteresis/cooldown.

    This class is kept as a compatibility adapter for existing callers/tests.
    Internally, it delegates decisions to the pure domain `GearboxPolicy` using
    explicit `GearboxState`.
    """

    def __init__(
        self,
        *,
        config: GearboxConfig | None = None,
        initial: Gear = Gear.G0,
        now_s: Callable[[], float] | None = None,
    ) -> None:
        self._config = config or GearboxConfig()
        self._gear = initial
        self._now_s = now_s or (lambda: 0.0)

        self._last_shift_s = float(self._now_s())
        self._state = DomainGearboxState(
            gear=DomainGear(int(self._gear)),
            last_shift_s=self._last_shift_s,
        )

        self._policy = GearboxPolicy(
            config=DomainGearboxConfig(
                q_high=self._config.q_high,
                q_block=self._config.q_block,
                g5_to_g2_cooldown_s=self._config.g5_to_g2_cooldown_s,
            )
        )

    @property
    def gear(self) -> Gear:
        return self._gear

    def domain_state(self) -> DomainGearboxState:
        """
        Snapshot of the current gearbox state in domain form.

        This is used by application adapters so orchestration can stay
        in terms of the domain model.
        """
        return self._state

    def evaluate(
        self,
        *,
        temp_c: float,
        vram_pct: float,
        depth: int,
        now_s: float | None = None,
    ) -> Gear:
        t = float(temp_c)
        v = float(vram_pct)
        d = int(depth)
        n = float(now_s if now_s is not None else self._now_s())

        transition = self._policy.decide(
            state=self._state,
            temp_c=t,
            vram_pct=v,
            depth=d,
            now_s=n,
        )

        self._state = transition.next_state
        self._gear = Gear(int(self._state.gear))
        self._last_shift_s = float(self._state.last_shift_s)
        return self._gear
