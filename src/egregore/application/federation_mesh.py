"""SEL-X federation mesh: cross-signing, trust propagation, malicious-node detection."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from egregore.interface.federation_ports import (
    CrossSignMessage,
    ISigningBackend,
    ITrustMeshStore,
    NodeTrustState,
)


class TrustMeshError(Exception):
    pass


@dataclass
class InMemoryTrustMeshStore:
    _states: dict[str, NodeTrustState] = field(default_factory=dict)

    def upsert(self, state: NodeTrustState) -> None:
        self._states[state.node_id] = state

    def get(self, node_id: str) -> NodeTrustState | None:
        return self._states.get(node_id)

    def get_all(self) -> Sequence[NodeTrustState]:
        return tuple(self._states.values())


class FederationMesh:
    """Cross-signing and trust mesh manager."""

    def __init__(
        self,
        *,
        node_id: str,
        signing_backend: ISigningBackend,
        trust_store: ITrustMeshStore,
        ban_threshold: int = 3,
        suspect_threshold: int = 1,
    ) -> None:
        self._node_id = node_id
        self._signing = signing_backend
        self._trust_store = trust_store
        self._ban_threshold = ban_threshold
        self._suspect_threshold = suspect_threshold
        # Hidden tracking for cumulative trust score calculation
        self._trust_counters: dict[str, tuple[int, int]] = (
            {}
        )  # node_id -> (successes, total)

    @property
    def node_id(self) -> str:
        return self._node_id

    def cross_sign(self, payload_hash: str) -> CrossSignMessage:
        """Produce a signed attestation over a payload hash."""
        timestamp_ns = time.time_ns()
        signature = self._signing.sign(payload_hash)
        return CrossSignMessage(
            node_id=self._node_id,
            payload_hash=payload_hash,
            signature=signature,
            timestamp_ns=timestamp_ns,
            public_key_fingerprint=self._signing.fingerprint(),
        )

    def verify_cross_sign(self, message: CrossSignMessage) -> bool:
        """Verify a cross-signed message and update trust state."""
        valid = self._signing.verify(
            message.payload_hash,
            message.signature,
            message.public_key_fingerprint,
        )
        self._update_trust_state(message.node_id, valid, message.timestamp_ns)
        return valid

    def trust_mesh(self) -> Sequence[NodeTrustState]:
        """Return the current trust state for all known nodes."""
        return self._trust_store.get_all()

    def malicious_node_detection(self) -> list[str]:
        """Return node IDs that exceed the ban threshold."""
        banned: list[str] = []
        for state in self._trust_store.get_all():
            if state.violation_count >= self._ban_threshold or state.status == "BANNED":
                banned.append(state.node_id)
        return banned

    def _update_trust_state(self, node_id: str, valid: bool, timestamp_ns: int) -> None:
        existing = self._trust_store.get(node_id)
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
            if violations >= self._ban_threshold:
                status = "BANNED"
            elif violations >= self._suspect_threshold and trust_score < 0.5:
                # Only SUSPECT if both violations exist AND trust score is low
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
        self._trust_store.upsert(state)
