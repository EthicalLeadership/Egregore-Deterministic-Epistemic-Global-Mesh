"""
BLACKSTAR LAW: Distributed Scheduler
Orchestrates local epoch scheduling + remote node placement.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from egregore.application.placement_scorer import PlacementScorer
from egregore.domain.node_profile import NodeProfile
from egregore.domain.work_unit import WorkUnit
from egregore.infrastructure.inter_node_messenger import InterNodeMessenger
from egregore.kernel.scheduler.epoch_scheduler import EpochScheduler


@dataclass
class DistributedScheduler:
    """
    Two-tier scheduling:
    1. Local epoch scheduler for same-node work units.
    2. Placement scorer + messenger for remote dispatch.
    """

    node_id: str
    local_scheduler: EpochScheduler
    placement_scorer: PlacementScorer
    messenger: InterNodeMessenger

    _node_profiles: dict[str, NodeProfile] = field(default_factory=dict)
    _dispatched_remote: list[str] = field(default_factory=list)

    def register_node(self, profile: NodeProfile) -> None:
        self._node_profiles[profile.node_id] = profile

    def remove_node(self, node_id: str) -> None:
        self._node_profiles.pop(node_id, None)

    def submit(self, work_unit: WorkUnit, timestamp_ns: int) -> bool:
        # Try local first
        if self.local_scheduler.submit(work_unit, timestamp_ns):
            return True

        # Local full — try remote placement
        remote_nodes = [
            p for p in self._node_profiles.values() if p.node_id != self.node_id
        ]
        if not remote_nodes:
            return False

        score = self.placement_scorer.score(work_unit, remote_nodes)
        if score is None:
            return False

        # Dispatch to remote
        self.messenger.dispatch_work_unit(score.node_id, work_unit)
        self._dispatched_remote.append(work_unit.work_unit_id)
        return True

    def get_cluster_status(self) -> dict:
        return {
            "local_node": self.node_id,
            "local_epoch": self.local_scheduler.epoch_number,
            "local_admitted": len(self.local_scheduler.admitted),
            "local_rejected": len(self.local_scheduler.rejected),
            "remote_nodes": len(self._node_profiles),
            "remote_dispatched": len(self._dispatched_remote),
            "node_profiles": {
                nid: p.to_canonical() for nid, p in self._node_profiles.items()
            },
        }

    def list_nodes(self) -> Sequence[str]:
        return tuple(self._node_profiles.keys())
