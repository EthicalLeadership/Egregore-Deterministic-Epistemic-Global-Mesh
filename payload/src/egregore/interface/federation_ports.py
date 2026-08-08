"""Ports for SEL-X federation / cross-node trust mesh."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CrossSignMessage:
    node_id: str
    payload_hash: str
    signature: str
    timestamp_ns: int
    public_key_fingerprint: str


@dataclass(frozen=True)
class NodeTrustState:
    node_id: str
    public_key_fingerprint: str | None
    trust_score: float
    last_seen_ns: int
    violation_count: int
    status: str  # "HEALTHY", "SUSPECT", "BANNED"


class ITrustMeshStore(Protocol):
    def upsert(self, state: NodeTrustState) -> None: ...
    def get(self, node_id: str) -> NodeTrustState | None: ...
    def get_all(self) -> Sequence[NodeTrustState]: ...


class ISigningBackend(Protocol):
    def fingerprint(self) -> str: ...
    def sign(self, payload_hash: str) -> str: ...
    def verify(self, payload_hash: str, signature: str, fingerprint: str) -> bool: ...
