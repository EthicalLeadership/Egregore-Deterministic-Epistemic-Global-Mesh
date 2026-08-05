# epistemic marker: provenance / non-repudiation
"""Witness-cosigned checkpoints (transparency-log semantics).

A checkpoint commits the `.zarc` chain state — head hash, entry count, and
a Merkle root over per-entry hashes — and is signed by the origin node
plus k-of-n independent witnesses. This converts "we say the chain looked
like this" into "independent witnesses vouch the chain looked like this",
which is the corroboration layer courts look for.

Pure domain module: signature verification is injected as a callable; no
I/O, no wall-clock.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from egregore.shared.canonical import sha256_hex


class WitnessError(Exception):
    """Fail-closed witness/quorum violation."""


@dataclass(frozen=True)
class Cosignature:
    """A single witness attestation over the checkpoint payload hash."""

    node_id: str
    fingerprint: str  # Ed25519 verify-key hex of the witness
    signature: str  # Ed25519 signature hex over the payload hash bytes
    timestamp_ns: int


@dataclass(frozen=True)
class WitnessCheckpoint:
    checkpoint_id: str
    chain_head_hash: str
    entry_count: int
    entries_merkle_root: str
    timestamp_ns: int
    origin_fingerprint: str
    origin_signature: str
    cosignatures: tuple[Cosignature, ...] = field(default_factory=tuple)

    def payload_hash(self) -> str:
        """The digest that origin and witnesses sign."""
        return checkpoint_payload_hash(
            checkpoint_id=self.checkpoint_id,
            chain_head_hash=self.chain_head_hash,
            entry_count=self.entry_count,
            entries_merkle_root=self.entries_merkle_root,
            timestamp_ns=self.timestamp_ns,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "chain_head_hash": self.chain_head_hash,
            "entry_count": self.entry_count,
            "entries_merkle_root": self.entries_merkle_root,
            "timestamp_ns": self.timestamp_ns,
            "origin_fingerprint": self.origin_fingerprint,
            "origin_signature": self.origin_signature,
            "cosignatures": [
                {
                    "node_id": c.node_id,
                    "fingerprint": c.fingerprint,
                    "signature": c.signature,
                    "timestamp_ns": c.timestamp_ns,
                }
                for c in self.cosignatures
            ],
        }


def derive_checkpoint_id(
    chain_head_hash: str, entries_merkle_root: str, timestamp_ns: int
) -> str:
    """Deterministic checkpoint identity."""
    return sha256_hex(
        f"checkpoint|{chain_head_hash}|{entries_merkle_root}|{timestamp_ns}".encode()
    )


def checkpoint_payload_hash(
    *,
    checkpoint_id: str,
    chain_head_hash: str,
    entry_count: int,
    entries_merkle_root: str,
    timestamp_ns: int,
) -> str:
    """Canonical digest over checkpoint content (the signed payload)."""
    material = (
        f"{checkpoint_id}|{chain_head_hash}|{entry_count}|"
        f"{entries_merkle_root}|{timestamp_ns}"
    )
    return sha256_hex(material.encode())


def validate_quorum(
    checkpoint: WitnessCheckpoint,
    *,
    min_witnesses: int,
    trusted_fingerprints: Mapping[str, str] | frozenset[str] | set[str],
    verify: Callable[[str, str, str], bool],
) -> None:
    """Validate origin signature + witness quorum. Fail-closed.

    Args:
        min_witnesses: required count of distinct valid cosignatures.
        trusted_fingerprints: either a mapping node_id -> fingerprint, or a
            set of trusted fingerprints. Cosignatures from anything else
            are rejected.
        verify: callable(payload_hash, signature_hex, fingerprint) -> bool
            (matches ``ISigningBackend.verify``).
    """
    payload_hash = checkpoint.payload_hash()

    if not checkpoint.origin_fingerprint:
        raise WitnessError("Checkpoint missing origin fingerprint")
    if not verify(
        payload_hash, checkpoint.origin_signature, checkpoint.origin_fingerprint
    ):
        raise WitnessError("Origin signature invalid")

    if isinstance(trusted_fingerprints, Mapping):
        trusted = dict(trusted_fingerprints)
    else:
        trusted = {fp: fp for fp in trusted_fingerprints}

    seen: set[str] = set()
    valid = 0
    for cosig in checkpoint.cosignatures:
        expected_node = None
        for node_id, fp in trusted.items():
            if fp == cosig.fingerprint:
                expected_node = node_id
                break
        if expected_node is None:
            raise WitnessError(
                f"Cosignature from untrusted fingerprint: {cosig.fingerprint[:16]}…"
            )
        if cosig.node_id != expected_node:
            raise WitnessError(
                f"Cosignature node {cosig.node_id!r} does not match registered "
                f"node {expected_node!r} for fingerprint"
            )
        if cosig.fingerprint in seen:
            raise WitnessError(
                f"Duplicate cosignature from {cosig.node_id!r} counts once"
            )
        if not verify(payload_hash, cosig.signature, cosig.fingerprint):
            raise WitnessError(f"Cosignature from {cosig.node_id!r} invalid")
        seen.add(cosig.fingerprint)
        valid += 1

    if valid < min_witnesses:
        raise WitnessError(
            f"Quorum not met: {valid} valid cosignature(s), "
            f"required {min_witnesses}"
        )
