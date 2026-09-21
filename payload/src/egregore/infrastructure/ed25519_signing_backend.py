"""Ed25519 signing backend implementation for federation mesh."""

from __future__ import annotations


class Ed25519SigningBackend:
    """Signing backend wrapping the existing kernel Ed25519 signer."""

    def __init__(self, signing_key_hex: str) -> None:
        self._signing_key_hex = signing_key_hex

    def fingerprint(self) -> str:
        from egregore.kernel.ed25519_signer import get_verify_key_hex

        return get_verify_key_hex(self._signing_key_hex)

    def sign(self, payload_hash: str) -> str:
        from egregore.kernel.ed25519_signer import sign_message

        return sign_message(
            signing_key_hex=self._signing_key_hex,
            message=payload_hash.encode("utf-8"),
        )

    def verify(self, payload_hash: str, signature: str, fingerprint: str) -> bool:
        from egregore.kernel.ed25519_signer import verify_message

        return verify_message(
            verify_key_hex=fingerprint,
            message=payload_hash.encode("utf-8"),
            signature_hex=signature,
        )
