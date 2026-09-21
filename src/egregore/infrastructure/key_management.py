"""Key management port and in-memory adapter for SEL-X.

Provides a clean boundary for HSM/KMS integration while shipping an
in-memory implementation suitable for testing and Phase 0 deployment.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


class KeyManagementError(Exception):
    pass


class KeyNotFoundError(KeyManagementError):
    pass


class KeyExpiredError(KeyManagementError):
    pass


@dataclass(frozen=True)
class KeyMaterial:
    key_id: str
    key_bytes: bytes
    algorithm: str
    created_at_ns: int
    expires_at_ns: int | None
    public_key_bytes: bytes | None = None


@dataclass(frozen=True)
class KeyRotationPolicy:
    ttl_seconds: int = 86400 * 90  # 90 days
    warning_before_expiry_seconds: int = 86400 * 7
    auto_rotate: bool = False


class IKeyManager(Protocol):
    """Port for key generation, retrieval, rotation, and health checks."""

    def generate_key(self, algorithm: str = "AES-256-GCM") -> str: ...
    def get_key(self, key_id: str) -> KeyMaterial: ...
    def get_public_key(self, key_id: str) -> bytes: ...
    def rotate_key(self, key_id: str) -> str: ...
    def list_key_ids(self) -> Sequence[str]: ...
    def health_check(self) -> Mapping[str, Any]: ...


@dataclass
class InMemoryKeyManager:
    """In-memory key manager with rotation schedule tracking."""

    rotation_policy: KeyRotationPolicy = field(default_factory=KeyRotationPolicy)

    def __post_init__(self) -> None:
        self._keys: dict[str, KeyMaterial] = {}
        self._key_history: dict[str, Sequence[str]] = {}

    def generate_key(self, algorithm: str = "AES-256-GCM") -> str:
        key_id = self._make_key_id(algorithm)
        if algorithm == "AES-256-GCM":
            key_bytes = secrets.token_bytes(32)
            public_key_bytes = None
        elif algorithm == "Ed25519":
            from nacl.signing import SigningKey

            signing_key = SigningKey.generate()
            key_bytes = signing_key.encode()
            public_key_bytes = signing_key.verify_key.encode()
        else:
            raise KeyManagementError(f"Unsupported algorithm: {algorithm}")

        now_ns = time.time_ns()
        ttl_ns = self.rotation_policy.ttl_seconds * 1_000_000_000
        key = KeyMaterial(
            key_id=key_id,
            key_bytes=key_bytes,
            algorithm=algorithm,
            created_at_ns=now_ns,
            expires_at_ns=now_ns + ttl_ns,
            public_key_bytes=public_key_bytes,
        )
        self._keys[key_id] = key
        self._key_history[key_id] = [key_id]
        return key_id

    def get_key(self, key_id: str) -> KeyMaterial:
        key = self._keys.get(key_id)
        if key is None:
            raise KeyNotFoundError(f"Key not found: {key_id}")
        if key.expires_at_ns is not None and time.time_ns() > key.expires_at_ns:
            raise KeyExpiredError(f"Key expired: {key_id}")
        return key

    def get_public_key(self, key_id: str) -> bytes:
        key = self.get_key(key_id)
        if key.public_key_bytes is None:
            raise KeyManagementError(f"Key {key_id} has no public component")
        return key.public_key_bytes

    def rotate_key(self, key_id: str) -> str:
        old_key = self.get_key(key_id)
        new_id = self.generate_key(algorithm=old_key.algorithm)
        history = list(self._key_history.get(key_id, []))
        history.append(new_id)
        self._key_history[key_id] = history
        return new_id

    def list_key_ids(self) -> Sequence[str]:
        return tuple(self._keys.keys())

    def health_check(self) -> dict[str, Any]:
        now_ns = time.time_ns()
        total = len(self._keys)
        expired = sum(
            1
            for k in self._keys.values()
            if k.expires_at_ns is not None and now_ns > k.expires_at_ns
        )
        expiring_soon = sum(
            1
            for k in self._keys.values()
            if k.expires_at_ns is not None
            and 0
            < k.expires_at_ns - now_ns
            < self.rotation_policy.warning_before_expiry_seconds * 1_000_000_000
        )
        return {
            "status": "HEALTHY" if expired == 0 else "DEGRADED",
            "total_keys": total,
            "expired_keys": expired,
            "expiring_soon": expiring_soon,
        }

    @staticmethod
    def _make_key_id(algorithm: str) -> str:
        rand = secrets.token_hex(8)
        return hashlib.sha256(
            f"{algorithm}:{rand}:{time.time_ns()}".encode()
        ).hexdigest()[:32]
