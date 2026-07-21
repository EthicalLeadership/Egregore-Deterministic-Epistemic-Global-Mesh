"""Ed25519 signing helpers exposed under an audit-friendly module name.

This module wraps the existing provenance-layer Ed25519 implementation in
``egregore.kernel.provenance`` so callers can sign and verify raw messages
without needing to manage a full .zarc chain.
"""

from __future__ import annotations

from nacl.signing import SigningKey, VerifyKey


def generate_signing_key() -> str:
    """Generate a new Ed25519 signing key and return its hex encoding."""
    return SigningKey.generate().encode().hex()


def sign_message(*, signing_key_hex: str, message: bytes) -> str:
    """Sign ``message`` with the given Ed25519 key; return signature hex."""
    signing_key = SigningKey(bytes.fromhex(signing_key_hex))
    return signing_key.sign(message).signature.hex()


def get_verify_key_hex(signing_key_hex: str) -> str:
    """Derive the verify-key hex for a given signing-key hex."""
    signing_key = SigningKey(bytes.fromhex(signing_key_hex))
    return signing_key.verify_key.encode().hex()


def verify_message(*, verify_key_hex: str, message: bytes, signature_hex: str) -> bool:
    """Verify an Ed25519 signature over ``message``."""
    try:
        verify_key = VerifyKey(bytes.fromhex(verify_key_hex))
        verify_key.verify(message, bytes.fromhex(signature_hex))
        return True
    except Exception:
        return False
