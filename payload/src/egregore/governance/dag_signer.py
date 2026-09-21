from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

from nacl.signing import SigningKey, VerifyKey

from egregore.shared.canonical import canonical_json, sha256_hex


@dataclass(frozen=True)
class DagSignature:
    key_hex: str
    sig_hex: str
    digest_hex: str


class DagSigner:
    """
    Ed25519 signer/verifier intended for provenance / governance DAG snapshots.

    Notes:
    - Signatures are over canonical JSON bytes of the “unsigned payload”.
    - digest_hex is SHA256 over the canonical JSON bytes (useful for audit).

    """

    def __init__(self, *, signing_key_hex: str) -> None:
        self._signing_key = SigningKey(bytes.fromhex(signing_key_hex))
        self._verify_key: VerifyKey = self._signing_key.verify_key

    @property
    def verify_key_hex(self) -> str:
        return self._verify_key.encode().hex()

    def sign(self, payload: Mapping[str, Any]) -> DagSignature:
        unsigned = dict(payload)
        unsigned_bytes = canonical_json(unsigned).encode("utf-8")
        sig = self._signing_key.sign(unsigned_bytes).signature.hex()
        digest_hex = sha256_hex(unsigned_bytes)
        return DagSignature(
            key_hex=self.verify_key_hex,
            sig_hex=sig,
            digest_hex=digest_hex,
        )

    def verify(self, payload: Mapping[str, Any], *, sig_hex: str) -> bool:
        unsigned_bytes = canonical_json(payload).encode("utf-8")
        sig_bytes = bytes.fromhex(sig_hex)
        try:
            self._verify_key.verify(unsigned_bytes, sig_bytes)
            return True
        except Exception:
            return False


def merge_payload_for_signature(
    base: Mapping[str, Any],
    *,
    digest_hex: str,
    sig_hex: str,
) -> Mapping[str, Any]:
    """
    Utility to produce a signed record for storage / DAG persistence.
    """
    out: MutableMapping[str, Any] = dict(base)
    out["digest_hex"] = digest_hex
    out["sig_hex"] = sig_hex
    return out
