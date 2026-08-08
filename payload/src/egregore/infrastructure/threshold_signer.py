"""
EGREGORE LAW: NaCl Threshold Signer
Concrete implementation using Shamir Secret Sharing + Ed25519.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from nacl.signing import SigningKey

from egregore.domain.threshold_signer import (
    ThresholdKeyMaterial,
    reconstruct_secret,
    split_secret,
)
from egregore.infrastructure.key_management import IKeyManager
from egregore.interface.threshold_signer_ports import (
    IThresholdSigner,
    ThresholdConfig,
    ThresholdSignature,
)


class ThresholdSignerError(Exception):
    pass


class InsufficientSharesError(ThresholdSignerError):
    pass


class ShareRetrievalError(ThresholdSignerError):
    pass


@dataclass
class NaClThresholdSigner(IThresholdSigner):
    key_manager: IKeyManager

    _threshold_keys: dict[str, ThresholdKeyMaterial] = field(default_factory=dict)
    _share_registry: dict[str, dict[int, bytes]] = field(default_factory=dict)

    def generate_threshold_key(self, config: ThresholdConfig) -> str:
        if config.key_algorithm != "Ed25519":
            raise ThresholdSignerError("Only Ed25519 supported")

        master_key = SigningKey.generate()
        master_secret = master_key.encode()
        master_public = master_key.verify_key.encode()

        shares = split_secret(master_secret, config.threshold, config.total_shares)

        key_id = hashlib.sha256(
            master_public + str(time.time_ns()).encode()
        ).hexdigest()[:32]

        self._threshold_keys[key_id] = ThresholdKeyMaterial(
            key_id=key_id,
            threshold=config.threshold,
            total_shares=config.total_shares,
            master_public_key=master_public,
        )
        self._share_registry[key_id] = shares

        return key_id

    def sign_with_threshold(
        self,
        *,
        key_id: str,
        message: bytes,
        share_indices: Sequence[int],
        timestamp_ns: int,
    ) -> ThresholdSignature:
        key_material = self._threshold_keys.get(key_id)
        if key_material is None:
            raise ThresholdSignerError(f"Threshold key not found: {key_id}")

        if len(share_indices) < key_material.threshold:
            raise InsufficientSharesError(
                f"Need {key_material.threshold} shares, got {len(share_indices)}"
            )

        shares = {}
        for idx in share_indices:
            share_bytes = self._share_registry.get(key_id, {}).get(idx)
            if share_bytes is None:
                raise ShareRetrievalError(f"Share {idx} not found for key {key_id}")
            shares[idx] = share_bytes

        master_secret = reconstruct_secret(shares)
        signing_key = SigningKey(master_secret)
        signature = signing_key.sign(message)

        return ThresholdSignature(
            signature_hex=signature.signature.hex(),
            key_id=key_id,
            threshold=key_material.threshold,
            participating_shares=list(share_indices),
            timestamp_ns=timestamp_ns,
            message_hash=hashlib.sha256(message).hexdigest(),
        )

    def get_public_key(self, key_id: str) -> bytes:
        key_material = self._threshold_keys.get(key_id)
        if key_material is None:
            raise ThresholdSignerError(f"Threshold key not found: {key_id}")
        return key_material.master_public_key

    def list_threshold_keys(self) -> Sequence[str]:
        return tuple(self._threshold_keys.keys())
