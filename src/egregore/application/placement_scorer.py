"""
BLACKSTAR LAW: Placement Scorer
Score nodes for work unit placement. Minimize waiting, not maximize utilization.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from egregore.domain.node_profile import NodeProfile
from egregore.domain.work_unit import WorkUnit


@dataclass(frozen=True, slots=True)
class PlacementScore:
    node_id: str
    score: float
    latency_ms: float
    capacity_remaining: float
    reason: str


class PlacementScorer:
    """
    Fail-closed placement: if no node can accept the work unit, return None.
    """

    def __init__(
        self,
        latency_weight: float = 0.3,
        capacity_weight: float = 0.5,
        utilization_weight: float = 0.2,
    ) -> None:
        self._latency_weight = latency_weight
        self._capacity_weight = capacity_weight
        self._utilization_weight = utilization_weight

    def score(
        self, work_unit: WorkUnit, nodes: Sequence[NodeProfile]
    ) -> PlacementScore | None:
        best: PlacementScore | None = None

        for node in nodes:
            # Hard filter: must have enough DT
            if node.dt_capacity < work_unit.demand.dt.value:
                continue

            # Hard filter: must have enough TU
            if node.tu_capacity < work_unit.demand.tu.value:
                continue

            # Soft scoring
            latency_score = max(0, 1.0 - (node.network_latency_ms / 100.0))
            capacity_score = node.capacity_score() / 1000.0  # normalize
            utilization_penalty = node.utilization_score()

            composite = (
                self._latency_weight * latency_score
                + self._capacity_weight * capacity_score
                - self._utilization_weight * utilization_penalty
            )

            if best is None or composite > best.score:
                best = PlacementScore(
                    node_id=node.node_id,
                    score=composite,
                    latency_ms=node.network_latency_ms,
                    capacity_remaining=node.dt_capacity - work_unit.demand.dt.value,
                    reason="placement_scored",
                )

        return best
