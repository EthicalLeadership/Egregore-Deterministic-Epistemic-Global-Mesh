"""Tests for the SEL-X key management port."""

from __future__ import annotations

import pytest

from egregore.infrastructure.key_management import (
    InMemoryKeyManager,
    KeyManagementError,
    KeyNotFoundError,
    KeyRotationPolicy,
)


def test_generate_aes_key() -> None:
    km = InMemoryKeyManager()
    key_id = km.generate_key("AES-256-GCM")
    assert key_id in km.list_key_ids()
    key = km.get_key(key_id)
    assert key.algorithm == "AES-256-GCM"
    assert len(key.key_bytes) == 32


def test_generate_ed25519_key() -> None:
    km = InMemoryKeyManager()
    key_id = km.generate_key("Ed25519")
    key = km.get_key(key_id)
    assert key.algorithm == "Ed25519"
    assert key.public_key_bytes is not None
    assert km.get_public_key(key_id) == key.public_key_bytes


def test_get_missing_key_raises() -> None:
    km = InMemoryKeyManager()
    with pytest.raises(KeyNotFoundError):
        km.get_key("missing")


def test_unsupported_algorithm_raises() -> None:
    km = InMemoryKeyManager()
    with pytest.raises(KeyManagementError):
        km.generate_key("RSA-2048")


def test_rotate_key_creates_new_key() -> None:
    km = InMemoryKeyManager()
    old_id = km.generate_key("AES-256-GCM")
    new_id = km.rotate_key(old_id)
    assert new_id != old_id
    assert new_id in km.list_key_ids()


def test_health_check_reports_expired_keys() -> None:
    km = InMemoryKeyManager(rotation_policy=KeyRotationPolicy(ttl_seconds=-1))
    km.generate_key("AES-256-GCM")
    health = km.health_check()
    assert health["status"] == "DEGRADED"
    assert health["expired_keys"] == 1
