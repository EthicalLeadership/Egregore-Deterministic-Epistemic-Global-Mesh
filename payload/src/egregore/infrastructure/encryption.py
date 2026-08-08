"""Symmetric encryption helpers exposed under an audit-friendly module name.

This module wraps the AES-256-GCM primitives already implemented in
``egregore.infrastructure.cluster_kek`` so callers can encrypt/decrypt
arbitrary data with a supplied key, independent of cluster KEK lifecycle.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_key() -> bytes:
    """Generate a random 256-bit key suitable for AES-GCM."""
    return AESGCM.generate_key(bit_length=256)


def encrypt(plaintext: bytes, key: bytes) -> dict[str, str]:
    """Encrypt ``plaintext`` with ``key`` using AES-256-GCM.

    Returns an envelope dict containing the nonce and ciphertext as hex.
    """
    if len(key) != 32:
        raise ValueError("key must be 32 bytes (256 bits)")
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return {
        "algorithm": "AES-256-GCM",
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
    }


def decrypt(envelope: dict[str, str], key: bytes) -> bytes:
    """Decrypt an envelope produced by ``encrypt``."""
    if len(key) != 32:
        raise ValueError("key must be 32 bytes (256 bits)")
    aesgcm = AESGCM(key)
    nonce = bytes.fromhex(envelope["nonce"])
    ciphertext = bytes.fromhex(envelope["ciphertext"])
    return aesgcm.decrypt(nonce, ciphertext, None)
