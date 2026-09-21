# epistemic marker: provenance / auditability
"""DOSS-06: Threat Intelligence Fusion — Federation mesh integrity and anomaly detection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass
class NodeTrustState:
    node_id: str
    public_key_fingerprint: str | None
    trust_score: float
    last_seen_ns: int
    violation_count: int
    status: str  # "HEALTHY", "SUSPECT", "BANNED"


@dataclass
class CrossSignMessage:
    node_id: str
    payload_hash: str
    signature: str
    timestamp_ns: int
    public_key_fingerprint: str


class InMemoryTrustMeshStore:
    def __init__(self) -> None:
        self._states: dict[str, NodeTrustState] = {}

    def upsert(self, state: NodeTrustState) -> None:
        self._states[state.node_id] = state

    def get(self, node_id: str) -> NodeTrustState | None:
        return self._states.get(node_id)

    def get_all(self) -> Sequence[NodeTrustState]:
        return tuple(self._states.values())


class ThreatIntelligenceFusion:
    """Cross-node threat intelligence fusion and anomaly detection."""

    def __init__(
        self,
        node_id: str,
        trust_store: InMemoryTrustMeshStore,
        ban_threshold: int = 3,
        suspect_threshold: int = 1,
    ) -> None:
        self.node_id = node_id
        self.trust_store = trust_store
        self.ban_threshold = ban_threshold
        self.suspect_threshold = suspect_threshold
        self._trust_counters: dict[str, tuple[int, int]] = {}

    def verify(self, message: CrossSignMessage) -> bool:
        # Simplified verification: signature must start with sig:
        valid = message.signature.startswith("sig:")
        self._update_trust_state(message.node_id, valid, message.timestamp_ns)
        return valid

    def _update_trust_state(self, node_id: str, valid: bool, timestamp_ns: int) -> None:
        existing = self.trust_store.get(node_id)
        if existing is None:
            state = NodeTrustState(
                node_id=node_id,
                public_key_fingerprint=None,
                trust_score=1.0 if valid else 0.0,
                last_seen_ns=timestamp_ns,
                violation_count=0 if valid else 1,
                status="HEALTHY" if valid else "SUSPECT",
            )
            self._trust_counters[node_id] = (1 if valid else 0, 1)
        else:
            violations = existing.violation_count + (0 if valid else 1)
            prev_successes, prev_total = self._trust_counters.get(
                node_id, (int(existing.trust_score * 10), 10)
            )
            cumulative_successes = prev_successes + (1 if valid else 0)
            total_attempts = prev_total + 1
            trust_score = round(cumulative_successes / total_attempts, 4)
            self._trust_counters[node_id] = (cumulative_successes, total_attempts)
            if violations >= self.ban_threshold:
                status = "BANNED"
            elif violations >= self.suspect_threshold and trust_score < 0.5:
                status = "SUSPECT"
            else:
                status = "HEALTHY"
            state = NodeTrustState(
                node_id=node_id,
                public_key_fingerprint=existing.public_key_fingerprint,
                trust_score=trust_score,
                last_seen_ns=timestamp_ns,
                violation_count=violations,
                status=status,
            )
        self.trust_store.upsert(state)

    def malicious_nodes(self) -> list[str]:
        return [
            state.node_id
            for state in self.trust_store.get_all()
            if state.violation_count >= self.ban_threshold or state.status == "BANNED"
        ]
