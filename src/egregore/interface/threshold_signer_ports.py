"""
BLACKSTAR LAW: Threshold Signer Interface Ports
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ThresholdSignature:
    signature_hex: str
    key_id: str
    threshold: int
    participating_shares: list[int]
    timestamp_ns: int
    message_hash: str


@dataclass(frozen=True)
class ThresholdConfig:
    threshold: int
    total_shares: int
    key_algorithm: str = "Ed25519"


class IThresholdSigner(Protocol):
    def generate_threshold_key(self, config: ThresholdConfig) -> str: ...

    def sign_with_threshold(
        self,
        *,
        key_id: str,
        message: bytes,
        share_indices: Sequence[int],
        timestamp_ns: int,
    ) -> ThresholdSignature: ...

    def get_public_key(self, key_id: str) -> bytes: ...

    def list_threshold_keys(self) -> Sequence[str]: ...
