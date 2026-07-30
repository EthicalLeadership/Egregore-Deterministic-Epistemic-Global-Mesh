# epistemic marker: provenance / auditability
"""
EGREGORE LAW: Node Profile
Capacity and health snapshot for distributed placement decisions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NodeProfile:
    node_id: str
    cpu_cores: int
    ram_mb: int
    dt_capacity: float
    tu_capacity: int
    network_latency_ms: float
    last_heartbeat_ns: int
    epoch_number: int = 0
    admitted_count: int = 0
    rejected_count: int = 0

    def utilization_score(self) -> float:
        """Lower is better. 0.0 = idle, 1.0 = saturated."""
        total = self.admitted_count + self.rejected_count
        if total == 0:
            return 0.0
        return self.rejected_count / total

    def capacity_score(self) -> float:
        """Higher is better. Raw capacity normalized."""
        return (self.cpu_cores * self.ram_mb * self.dt_capacity) / (
            self.tu_capacity + 1
        )

    def to_canonical(self) -> dict:
        return {
            "node_id": self.node_id,
            "cpu_cores": self.cpu_cores,
            "ram_mb": self.ram_mb,
            "dt_capacity": self.dt_capacity,
            "tu_capacity": self.tu_capacity,
            "network_latency_ms": self.network_latency_ms,
            "last_heartbeat_ns": self.last_heartbeat_ns,
            "epoch_number": self.epoch_number,
            "admitted_count": self.admitted_count,
            "rejected_count": self.rejected_count,
        }
