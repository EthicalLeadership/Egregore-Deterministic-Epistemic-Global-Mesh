"""Ports for placement policy decisions.

This lives in the interface layer so both application (orchestrator/policy)
and infrastructure (model host) can depend on the decision shape without
introducing a forbidden application<->infrastructure dependency.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlacementDecision:
    n_gpu_layers: int
    n_threads: int
    reason: str
