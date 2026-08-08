from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThermalSample:
    temp_c: float
    vram_pct: float
    depth: int
    now_s: float
