# epistemic marker: provenance / auditability
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Gear(IntEnum):
    G0 = 0
    G2 = 2
    G5 = 5


@dataclass(frozen=True)
class GearboxState:
    gear: Gear = Gear.G0
    last_shift_s: float = 0.0


@dataclass(frozen=True)
class GearboxTransition:
    next_state: GearboxState
    shifted: bool
