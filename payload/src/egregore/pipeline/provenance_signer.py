"""Provenance signer for pipeline governance records.

Attests that a module passed manifest validation and M1/M2 governance checks
by producing a signed, canonical record.  The signature can be verified later
against the signer's public key.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from egregore.shared.canonical import canonical_dumps

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sign_provenance(
    record: dict[str, Any],
    private_key: Ed25519PrivateKey,
    signer_id: str,
) -> dict[str, Any]:
    """Return *record* enriched with a signed provenance block.

    The canonical JSON serialization of *record* is hashed with SHA-256 and
    signed with the provided Ed25519 private key.  The returned dict contains
    the original data under the ``record`` key plus a ``provenance`` block
    with the hash, signature, signer, and timestamp.

    Args:
        record: The governance/validation record to attest (must be JSON
            serializable).
        private_key: An Ed25519 private key.
        signer_id: Identifier of the signer (e.g., key fingerprint or CI job).

    Returns:
        A dict containing the original record and a provenance block.

    """
    canonical_bytes = _canonical_bytes(record)
    digest = hashlib.sha256(canonical_bytes).hexdigest()
    signature_bytes = private_key.sign(canonical_bytes)

    return {
        "record": record,
        "provenance": {
            "algorithm": "ed25519+sha256",
            "hash": digest,
            "signature": f"ed25519:{signature_bytes.hex()}",
            "signer_id": signer_id,
            "timestamp_ns": time.time_ns(),
        },
    }


def verify_provenance(
    signed_record: dict[str, Any],
    public_key: Ed25519PublicKey,
) -> bool:
    """Verify the signature on a provenance record.

    Args:
        signed_record: The output of :func:`sign_provenance`.
        public_key: The Ed25519 public key matching the private key that
            signed the record.

    Returns:
        True if the signature is valid and the hash matches the canonical
        record; False otherwise.

    """
    record = signed_record.get("record")
    provenance = signed_record.get("provenance", {})
    signature_value = provenance.get("signature", "")

    if not record or not signature_value.startswith("ed25519:"):
        return False

    signature_bytes = bytes.fromhex(signature_value[len("ed25519:") :])
    canonical_bytes = _canonical_bytes(record)
    expected_hash = hashlib.sha256(canonical_bytes).hexdigest()

    if provenance.get("hash") != expected_hash:
        return False

    try:
        public_key.verify(signature_bytes, canonical_bytes)
    except Exception:  # noqa: BLE001
        return False
    return True


def load_private_key(path: Path | str) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from a PEM file.

    Args:
        path: Path to a PEM-encoded private key file.

    Returns:
        The loaded Ed25519 private key.

    """
    pem = Path(path).read_bytes()
    return serialization.load_pem_private_key(pem, password=None)


def load_public_key(path: Path | str) -> Ed25519PublicKey:
    """Load an Ed25519 public key from a PEM file.

    Args:
        path: Path to a PEM-encoded public key file.

    Returns:
        The loaded Ed25519 public key.

    """
    pem = Path(path).read_bytes()
    return serialization.load_pem_public_key(pem)


def generate_signing_key(output_dir: Path | str) -> tuple[str, str]:
    """Generate a new Ed25519 key pair and return the PEM strings.

    Args:
        output_dir: Directory where ``signing_key.pem`` and
            ``signing_key.pub`` will be written.

    Returns:
        A tuple of (private_key_pem, public_key_pem).

    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "signing_key.pem").write_text(private_pem)
    (out / "signing_key.pub").write_text(public_pem)

    return private_pem, public_pem


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _canonical_bytes(record: dict[str, Any]) -> bytes:
    """Return a deterministic UTF-8 JSON serialization of *record*."""
    return canonical_dumps(record).encode("utf-8")
