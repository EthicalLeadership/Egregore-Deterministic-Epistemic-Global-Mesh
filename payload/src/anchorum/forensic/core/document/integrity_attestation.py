"""ANCHORUM file integrity attestation.

Computes a SHA-256 manifest of an input document. Cryptographic signing is
intentionally a thin wrapper around the existing Egregore timestamp/provenance
module so we do not duplicate key handling here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IntegrityAttestation:
    file_hash: str
    algorithm: str
    is_valid: bool = True
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_hash": self.file_hash,
            "algorithm": self.algorithm,
            "is_valid": self.is_valid,
            "details": self.details or {},
        }


class IntegrityAttestor:
    """Generate file-level integrity attestations."""

    def attest(self, path: Path | str) -> IntegrityAttestation:
        pdf_path = Path(path)
        if not pdf_path.exists():
            return IntegrityAttestation(
                file_hash="",
                algorithm="sha256",
                is_valid=False,
                details={"error": "file not found"},
            )

        h = hashlib.sha256()
        with open(pdf_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
        return IntegrityAttestation(
            file_hash=digest,
            algorithm="sha256",
            is_valid=True,
            details={"size_bytes": pdf_path.stat().st_size},
        )
