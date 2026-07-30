# epistemic marker: provenance / auditability
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GearboxConfig:
    q_high: int = 100
    q_block: int = 500
    g5_to_g2_cooldown_s: float = 30.0
