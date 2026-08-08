"""NodeRegistry - Crust agency: catalog of nodes in the Mycelial network."""

import importlib
from dataclasses import dataclass, field
from typing import Any

from egregore.domain.job_models import NodeCapability, NodeHeartbeat
from egregore.interface.job_router_ports import INodeRegistry
from egregore.shared.ports import INodeStore

NodeTrustStore = None  # lazy import to avoid circular dependency


def _trust_store_cls() -> Any:
    global NodeTrustStore
    if NodeTrustStore is None:
        mod = importlib.import_module("egregore.infrastructure.redis_store")
        NodeTrustStore = mod.NodeTrustStore
    return NodeTrustStore


@dataclass
class InMemoryNodeStore:
    _nodes: dict[str, NodeCapability] = field(default_factory=dict)

    def upsert(self, node: NodeCapability) -> None:
        self._nodes[node.node_id] = node

    def get(self, node_id: str) -> NodeCapability | None:
        return self._nodes.get(node_id)

    def get_all(self) -> list[NodeCapability]:
        return list(self._nodes.values())

    def get_by_capability(self, capability: str) -> list[NodeCapability]:
        return [
            n
            for n in self._nodes.values()
            if capability in n.capabilities and n.status == "ACTIVE"
        ]

    def get_active(self, cutoff_ticks: int) -> list[NodeCapability]:
        return [
            n
            for n in self._nodes.values()
            if n.status == "ACTIVE" and n.last_heartbeat_ns >= cutoff_ticks
        ]

    def deprecate(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        old = self._nodes[node_id]
        from dataclasses import replace

        new = replace(old, status="OFFLINE")
        self._nodes[node_id] = new
        return True


class NodeRegistry(INodeRegistry):
    def __init__(
        self,
        store: INodeStore,
        heartbeat_timeout_ticks: int = 5,
        trust_store: Any = None,
        treaty_ledger: Any = None,
    ):
        self._store = store
        self._heartbeat_timeout = heartbeat_timeout_ticks
        self._trust_store = trust_store
        self._treaty_ledger = treaty_ledger
        self._evidence: dict[str, list[tuple]] = {}
        self._system_capabilities: list[str] = []
        self._load_evidence()

    def set_system_capabilities(self, caps: list[str]) -> None:
        self._system_capabilities = sorted(caps)

    def _load_evidence(self) -> None:
        if self._trust_store is None:
            return
        # We don't know all node ids in advance, so lazy-load per node during
        # heartbeat. This method is a no-op for now.

    def _evidence_for(self, node_id: str) -> list[tuple]:
        if node_id not in self._evidence and self._trust_store is not None:
            self._evidence[node_id] = self._trust_store.get_evidence(node_id)
        return self._evidence.get(node_id, [])

    def cooldown_node(self, node_id: str, ttl_seconds: int = 60) -> None:
        if self._trust_store is not None:
            self._trust_store.cooldown(node_id, ttl_seconds)

    def is_cooldown(self, node_id: str) -> bool:
        if self._trust_store is None:
            return False
        return bool(self._trust_store.is_cooldown(node_id))

    def heartbeat(self, pulse: NodeHeartbeat) -> None:
        trust = self._compute_trust(pulse.node_id, pulse)
        status = "ACTIVE"
        if self._treaty_ledger is not None:
            active = self._treaty_ledger.active_treaty()
            if active is not None and not self._treaty_ledger.has_ratified(
                pulse.node_id
            ):
                status = "TREATY_PENDING"
        node = NodeCapability(
            node_id=pulse.node_id,
            capabilities=sorted(pulse.available_capabilities),
            resource_profile=pulse.load_metrics,
            trust_score=trust,
            last_heartbeat_ns=pulse.timestamp_ns,
            status=status,
            public_key_fingerprint=pulse.public_key_fingerprint,
        )
        self._store.upsert(node)

    def get_available(self, capabilities: list[str]) -> list[NodeCapability]:
        all_nodes = self._store.get_all()
        available = []
        for node in all_nodes:
            if node.status != "ACTIVE":
                continue
            if self._treaty_ledger is not None:
                active = self._treaty_ledger.active_treaty()
                if active is not None and not self._treaty_ledger.has_ratified(
                    node.node_id
                ):
                    continue
            if self._has_all_capabilities(node, capabilities):
                available.append(node)
        available.sort(key=lambda n: (-n.trust_score, n.node_id))
        return available

    def get_node(self, node_id: str) -> NodeCapability | None:
        return self._store.get(node_id)

    def deprecate_stale(self, cutoff_ticks: int) -> list[str]:
        deprecated = []
        for node in self._store.get_all():
            if node.status == "ACTIVE" and node.last_heartbeat_ns < cutoff_ticks:
                self._store.deprecate(node.node_id)
                deprecated.append(node.node_id)
        return deprecated

    def record_evidence(self, node_id: str, success: bool, duration_ms: int) -> None:
        self._evidence.setdefault(node_id, []).append((success, duration_ms))
        if self._trust_store is not None:
            self._trust_store.record_evidence(node_id, success, duration_ms)

    def _compute_trust(self, node_id: str, pulse: NodeHeartbeat) -> float:
        evidence = self._evidence_for(node_id)
        if evidence:
            successes = sum(1 for s, _ in evidence if s)
            success_rate = successes / len(evidence)
        else:
            success_rate = 0.5
        freshness = 1.0
        if self._system_capabilities:
            available = set(pulse.available_capabilities)
            matched = sum(1 for cap in self._system_capabilities if cap in available)
            breadth = matched / len(self._system_capabilities)
        else:
            breadth = 1.0
        trust = (success_rate * 0.4) + (freshness * 0.3) + (breadth * 0.3)
        return round(trust, 4)

    @staticmethod
    def _has_all_capabilities(node: NodeCapability, required: list[str]) -> bool:
        if not required:
            return True
        available = set(node.capabilities)
        return all(cap in available for cap in required)
