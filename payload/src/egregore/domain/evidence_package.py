# epistemic marker: provenance / evidence-packaging
"""Sealed evidence package model (BagIt 1.0 profile) — pure domain.

Manifest formatting, seal identity, and the verification-report schema.
No I/O, no wall-clock: all content arrives as arguments.

Profile notes (RFC 8493 alignment):
- manifests are ``<hash><space><space><relative-path>`` lines, sorted by
  path for byte-level determinism;
- SHA-256 lowercase hex, same convention as the rest of the substrate;
- the package directory layout is documented in
  ``docs/evidence/sealed_packages.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from egregore.shared.canonical import sha256_hex

BAGIT_VERSION = "1.0"
BAGIT_TXT = "bagit.txt"
BAG_INFO_TXT = "bag-info.txt"
MANIFEST_NAME = "manifest-sha256.txt"
TAGMANIFEST_NAME = "tagmanifest-sha256.txt"
REPORT_NAME = "verification_report.json"
REPORT_SIG_NAME = "verification_report.sig"
SUBJECT_INDEX_NAME = "subject_index.json"

# Tag files covered by the tag manifest (everything outside data/).
TAG_FILES = (
    BAGIT_TXT,
    BAG_INFO_TXT,
    MANIFEST_NAME,
    REPORT_NAME,
    REPORT_SIG_NAME,
    SUBJECT_INDEX_NAME,
)


class EvidencePackageError(Exception):
    """Fail-closed package format violation."""


def format_manifest(entries: Mapping[str, str]) -> str:
    """BagIt manifest text: sorted ``<hash>  <path>`` lines."""
    return "".join(
        f"{entries[path]}  {path}\n" for path in sorted(entries)
    )


def parse_manifest(text: str) -> dict[str, str]:
    """Parse a manifest. Fail-closed on malformed lines."""
    entries: dict[str, str] = {}
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split("  ", 1)
        if len(parts) != 2 or not parts[0] or not parts[1].strip():
            raise EvidencePackageError(
                f"Malformed manifest line {line_no}: {raw!r}"
            )
        digest, path = parts[0], parts[1].strip()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise EvidencePackageError(
                f"Manifest line {line_no} has a non-SHA-256 digest"
            )
        entries[path] = digest
    return entries


def derive_seal_id(manifest_text: str) -> str:
    """Seal identity: SHA-256 over the payload fixity manifest.

    Computed from the manifest alone, so the signed report can carry the
    seal identity without a signature/seal-id circularity. The signer is
    bound by the report signature itself.
    """
    return sha256_hex(manifest_text.encode())


def payload_oxum(sizes: Mapping[str, int]) -> str:
    """BagIt Payload-Oxum: ``<totalbytes>.<filecount>``."""
    return f"{sum(sizes.values())}.{len(sizes)}"


@dataclass(frozen=True)
class EvidenceVerificationReport:
    """Machine-readable, signable verification report for a sealed package."""

    report_version: str
    seal_id: str
    generated_at_ns: int
    subject: Mapping[str, str]
    chains: tuple[Mapping[str, Any], ...]
    blocks: Mapping[str, Any] | None
    anchors: Mapping[str, Any] | None
    custody: Mapping[str, Any] | None
    witness: Mapping[str, Any] | None
    fixity_entries: int
    verdict: bool
    failures: tuple[str, ...] = ()
    signer_fingerprint: str = ""

    def to_canonical(self) -> dict[str, Any]:
        return {
            "__type__": "EvidenceVerificationReport",
            "report_version": self.report_version,
            "seal_id": self.seal_id,
            "generated_at_ns": self.generated_at_ns,
            "subject": dict(self.subject),
            "chains": [dict(c) for c in self.chains],
            "blocks": dict(self.blocks) if self.blocks else None,
            "anchors": dict(self.anchors) if self.anchors else None,
            "custody": dict(self.custody) if self.custody else None,
            "witness": dict(self.witness) if self.witness else None,
            "fixity_entries": self.fixity_entries,
            "verdict": self.verdict,
            "failures": list(self.failures),
            "signer_fingerprint": self.signer_fingerprint,
        }

    @staticmethod
    def from_canonical(obj: Mapping[str, Any]) -> EvidenceVerificationReport:
        if obj.get("__type__") != "EvidenceVerificationReport":
            raise EvidencePackageError("Not an EvidenceVerificationReport")
        return EvidenceVerificationReport(
            report_version=str(obj["report_version"]),
            seal_id=str(obj["seal_id"]),
            generated_at_ns=int(obj["generated_at_ns"]),
            subject=dict(obj.get("subject") or {}),
            chains=tuple(dict(c) for c in obj.get("chains") or ()),
            blocks=obj.get("blocks"),
            anchors=obj.get("anchors"),
            custody=obj.get("custody"),
            witness=obj.get("witness"),
            fixity_entries=int(obj["fixity_entries"]),
            verdict=bool(obj["verdict"]),
            failures=tuple(obj.get("failures") or ()),
            signer_fingerprint=str(obj.get("signer_fingerprint", "")),
        )
