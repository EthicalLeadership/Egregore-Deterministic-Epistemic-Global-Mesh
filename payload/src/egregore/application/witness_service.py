"""Witness service — cosigned checkpoints over the `.zarc` chain.

Creates transparency-log-style checkpoints: a Merkle commitment over the
chain's per-entry hashes, signed by the origin node's ``ISigningBackend``
(HSM-capable) and cosigned by a quorum of independent witnesses. The
checkpoint is itself appended to the chain (standard self-commitment), so
it inherits the chain's integrity.

Auditors get two primitives:
- ``prove_entry`` / ``verify_entry_inclusion`` — prove a single `.zarc`
  line was committed in a cosigned checkpoint without exposing the rest
  of the chain;
- ``validate_quorum`` (domain) — verify a checkpoint's origin signature
  and witness quorum offline, given only trusted fingerprints.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from egregore.domain.witness_checkpoint import (
    Cosignature,
    WitnessCheckpoint,
    WitnessError,
    checkpoint_payload_hash,
    derive_checkpoint_id,
    validate_quorum,
)
from egregore.kernel.provenance import Provenance
from egregore.shared.canonical import sha256_hex
from egregore.shared.merkle import InclusionProof, MerkleTree

GENESIS_HASH = "0" * 64


class ISigningBackend(Protocol):
    def fingerprint(self) -> str: ...
    def sign(self, payload_hash: str) -> str: ...
    def verify(self, payload_hash: str, signature: str, fingerprint: str) -> bool: ...


class IWitness(Protocol):
    """A cosigning party (local key, federation peer, HSM)."""

    def cosign(self, payload_hash: str, timestamp_ns: int) -> Cosignature: ...


@dataclass(frozen=True)
class EntryInclusionProof:
    """Auditor-facing proof that one chain entry is inside a checkpoint."""

    checkpoint_id: str
    entry_index: int
    entry_hash: str
    inclusion_proof: InclusionProof

    def to_canonical(self) -> dict[str, Any]:
        return {
            "__type__": "EntryInclusionProof",
            "checkpoint_id": self.checkpoint_id,
            "entry_index": self.entry_index,
            "entry_hash": self.entry_hash,
            "inclusion_proof": {
                "leaf_index": self.inclusion_proof.leaf_index,
                "leaf_hash": self.inclusion_proof.leaf_hash,
                "sibling_hashes": list(self.inclusion_proof.sibling_hashes),
                "root_hash": self.inclusion_proof.root_hash,
            },
        }


def _entry_hashes(provenance: Provenance) -> list[str]:
    return [
        sha256_hex((line + "\n").encode("utf-8"))
        for line in provenance.iter_lines()
    ]


def _entry_merkle(entry_hashes: Sequence[str]) -> MerkleTree:
    leaves = [h.encode("utf-8") for h in entry_hashes]
    return MerkleTree(leaves)


class WitnessService:
    """Creates, stores, and proves witness-cosigned checkpoints."""

    def __init__(
        self,
        *,
        provenance: Provenance,
        signing_backend: ISigningBackend,
        witnesses: Sequence[IWitness] = (),
        trusted_fingerprints: Mapping[str, str] | None = None,
        min_witnesses: int = 0,
    ) -> None:
        self._provenance = provenance
        self._backend = signing_backend
        self._witnesses = tuple(witnesses)
        self._trusted = dict(trusted_fingerprints or {})
        self._min_witnesses = min_witnesses

    def create_checkpoint(self, timestamp_ns: int) -> WitnessCheckpoint:
        """Commit the current chain state into a cosigned checkpoint."""
        entry_hashes = _entry_hashes(self._provenance)
        if not entry_hashes:
            raise WitnessError("Cannot checkpoint an empty chain")
        chain_head = entry_hashes[-1]
        tree = _entry_merkle(entry_hashes)
        merkle_root = tree.root_hash
        if merkle_root is None:
            raise WitnessError("Merkle root computation failed")

        checkpoint_id = derive_checkpoint_id(chain_head, merkle_root, timestamp_ns)
        payload_hash = checkpoint_payload_hash(
            checkpoint_id=checkpoint_id,
            chain_head_hash=chain_head,
            entry_count=len(entry_hashes),
            entries_merkle_root=merkle_root,
            timestamp_ns=timestamp_ns,
        )

        origin_signature = self._backend.sign(payload_hash)
        cosignatures = tuple(
            witness.cosign(payload_hash, timestamp_ns) for witness in self._witnesses
        )
        checkpoint = WitnessCheckpoint(
            checkpoint_id=checkpoint_id,
            chain_head_hash=chain_head,
            entry_count=len(entry_hashes),
            entries_merkle_root=merkle_root,
            timestamp_ns=timestamp_ns,
            origin_fingerprint=self._backend.fingerprint(),
            origin_signature=origin_signature,
            cosignatures=cosignatures,
        )

        # Fail-closed: an invalid checkpoint is never committed to the chain.
        validate_quorum(
            checkpoint,
            min_witnesses=self._min_witnesses,
            trusted_fingerprints=self._trusted,
            verify=self._backend.verify,
        )

        self._provenance.append(
            engine="witness",
            event="witness.checkpoint",
            payload=checkpoint.to_payload(),
            ts_ns=timestamp_ns,
        )
        return checkpoint

    def prove_entry(self, entry_index: int) -> EntryInclusionProof:
        """Prove chain entry ``entry_index`` is inside the latest checkpoint."""
        checkpoints = self._checkpoints()
        if not checkpoints:
            raise WitnessError("No checkpoints on this chain")
        checkpoint = checkpoints[-1]
        if not (0 <= entry_index < checkpoint.entry_count):
            raise WitnessError(
                f"entry_index {entry_index} outside checkpointed range "
                f"0..{checkpoint.entry_count - 1}"
            )
        entry_hashes = _entry_hashes(self._provenance)
        tree = _entry_merkle(entry_hashes[: checkpoint.entry_count])
        return EntryInclusionProof(
            checkpoint_id=checkpoint.checkpoint_id,
            entry_index=entry_index,
            entry_hash=entry_hashes[entry_index],
            inclusion_proof=tree.inclusion_proof(entry_index),
        )

    @staticmethod
    def verify_entry_inclusion(
        proof: EntryInclusionProof, checkpoint: WitnessCheckpoint
    ) -> bool:
        """Verify an inclusion proof against a checkpoint's Merkle root."""
        if proof.checkpoint_id != checkpoint.checkpoint_id:
            return False
        if proof.inclusion_proof.root_hash != checkpoint.entries_merkle_root:
            return False
        return MerkleTree.verify_inclusion_proof(
            proof.inclusion_proof, proof.entry_hash.encode("utf-8")
        )

    def _checkpoints(self) -> list[WitnessCheckpoint]:
        checkpoints: list[WitnessCheckpoint] = []
        for entry in self._provenance.iter_entries():
            if entry.engine != "witness" or entry.event != "witness.checkpoint":
                continue
            payload = entry.payload
            checkpoints.append(
                WitnessCheckpoint(
                    checkpoint_id=str(payload["checkpoint_id"]),
                    chain_head_hash=str(payload["chain_head_hash"]),
                    entry_count=int(payload["entry_count"]),
                    entries_merkle_root=str(payload["entries_merkle_root"]),
                    timestamp_ns=int(payload["timestamp_ns"]),
                    origin_fingerprint=str(payload["origin_fingerprint"]),
                    origin_signature=str(payload["origin_signature"]),
                    cosignatures=tuple(
                        Cosignature(
                            node_id=str(c["node_id"]),
                            fingerprint=str(c["fingerprint"]),
                            signature=str(c["signature"]),
                            timestamp_ns=int(c["timestamp_ns"]),
                        )
                        for c in payload.get("cosignatures", [])
                    ),
                )
            )
        return checkpoints
