"""Tests for witness-cosigned checkpoints over the .zarc chain."""

from __future__ import annotations

from pathlib import Path

import pytest

from egregore.application.witness_service import WitnessService
from egregore.domain.witness_checkpoint import (
    Cosignature,
    WitnessError,
    validate_quorum,
)
from egregore.infrastructure.ed25519_signing_backend import Ed25519SigningBackend
from egregore.kernel.ed25519_signer import generate_signing_key, get_verify_key_hex
from egregore.kernel.provenance import Provenance


class _LocalWitness:
    """A witness holding its own Ed25519 key (stands in for a peer node)."""

    def __init__(self, node_id: str, signing_key_hex: str):
        self.node_id = node_id
        self._backend = Ed25519SigningBackend(signing_key_hex)

    @property
    def fingerprint(self) -> str:
        return self._backend.fingerprint()

    def cosign(self, payload_hash: str, timestamp_ns: int) -> Cosignature:
        return Cosignature(
            node_id=self.node_id,
            fingerprint=self.fingerprint,
            signature=self._backend.sign(payload_hash),
            timestamp_ns=timestamp_ns,
        )


def _setup(tmp_path: Path, n_witnesses: int = 2, chain_key: str | None = None):
    provenance = Provenance(
        tmp_path / "chain.zarc", signing_key_hex=chain_key or generate_signing_key()
    )
    for i in range(5):
        provenance.append(
            engine="test", event=f"event.{i}", payload={"i": i}, ts_ns=1000 + i
        )
    backend = Ed25519SigningBackend(generate_signing_key())
    witnesses = [_LocalWitness(f"node-{i}", generate_signing_key()) for i in range(n_witnesses)]
    trusted = {w.node_id: w.fingerprint for w in witnesses}
    service = WitnessService(
        provenance=provenance,
        signing_backend=backend,
        witnesses=witnesses,
        trusted_fingerprints=trusted,
        min_witnesses=n_witnesses,
    )
    return service, provenance, backend, witnesses, trusted


class TestCheckpointCreation:
    def test_checkpoint_committed_and_valid(self, tmp_path: Path):
        service, provenance, *_ = _setup(tmp_path)
        checkpoint = service.create_checkpoint(timestamp_ns=5000)
        assert checkpoint.entry_count == 5
        assert len(checkpoint.cosignatures) == 2
        # The checkpoint itself is a chain entry; chain still verifies.
        assert provenance.verify_chain()
        events = [
            e for e in provenance.iter_entries()
            if e.engine == "witness" and e.event == "witness.checkpoint"
        ]
        assert len(events) == 1

    def test_checkpoint_deterministic(self, tmp_path: Path):
        chain_key = generate_signing_key()  # same chain key => same lines
        service1, *_ = _setup(tmp_path / "a", chain_key=chain_key)
        service2, *_ = _setup(tmp_path / "b", chain_key=chain_key)
        # Same chain content + same timestamp => same checkpoint id/root.
        cp1 = service1.create_checkpoint(timestamp_ns=5000)
        cp2 = service2.create_checkpoint(timestamp_ns=5000)
        assert cp1.entries_merkle_root == cp2.entries_merkle_root
        assert cp1.checkpoint_id == cp2.checkpoint_id

    def test_empty_chain_rejected(self, tmp_path: Path):
        provenance = Provenance(
            tmp_path / "empty.zarc", signing_key_hex=generate_signing_key()
        )
        service = WitnessService(
            provenance=provenance,
            signing_backend=Ed25519SigningBackend(generate_signing_key()),
        )
        with pytest.raises(WitnessError, match="empty chain"):
            service.create_checkpoint(timestamp_ns=1)

    def test_quorum_not_met_refuses_commit(self, tmp_path: Path):
        service, provenance, *_ = _setup(tmp_path, n_witnesses=1)
        service._min_witnesses = 2  # require more than available
        with pytest.raises(WitnessError, match="Quorum not met"):
            service.create_checkpoint(timestamp_ns=5000)
        # Nothing committed after refusal.
        assert not [
            e for e in provenance.iter_entries() if e.engine == "witness"
        ]


class TestQuorumValidation:
    def test_untrusted_witness_rejected(self, tmp_path: Path):
        service, _, backend, witnesses, trusted = _setup(tmp_path)
        checkpoint = service.create_checkpoint(timestamp_ns=5000)
        intruder = _LocalWitness("node-evil", generate_signing_key())
        forged = Cosignature(
            node_id="node-evil",
            fingerprint=intruder.fingerprint,
            signature=intruder._backend.sign(checkpoint.payload_hash()),
            timestamp_ns=5000,
        )
        bad = type(checkpoint)(
            **{**checkpoint.__dict__, "cosignatures": (forged,)}
        )
        with pytest.raises(WitnessError, match="untrusted fingerprint"):
            validate_quorum(
                bad,
                min_witnesses=1,
                trusted_fingerprints=trusted,
                verify=backend.verify,
            )

    def test_invalid_cosignature_rejected(self, tmp_path: Path):
        service, _, backend, witnesses, trusted = _setup(tmp_path)
        checkpoint = service.create_checkpoint(timestamp_ns=5000)
        cosig = checkpoint.cosignatures[0]
        bad_cosig = Cosignature(
            node_id=cosig.node_id,
            fingerprint=cosig.fingerprint,
            signature="00" * 64,
            timestamp_ns=cosig.timestamp_ns,
        )
        bad = type(checkpoint)(
            **{**checkpoint.__dict__, "cosignatures": (bad_cosig,)}
        )
        with pytest.raises(WitnessError, match="invalid"):
            validate_quorum(
                bad,
                min_witnesses=1,
                trusted_fingerprints=trusted,
                verify=backend.verify,
            )

    def test_duplicate_witness_counts_once(self, tmp_path: Path):
        service, _, backend, witnesses, trusted = _setup(tmp_path, n_witnesses=1)
        checkpoint = service.create_checkpoint(timestamp_ns=5000)
        doubled = type(checkpoint)(
            **{
                **checkpoint.__dict__,
                "cosignatures": checkpoint.cosignatures * 2,
            }
        )
        with pytest.raises(WitnessError, match="Duplicate"):
            validate_quorum(
                doubled,
                min_witnesses=2,
                trusted_fingerprints=trusted,
                verify=backend.verify,
            )

    def test_bad_origin_signature_rejected(self, tmp_path: Path):
        service, _, backend, _, trusted = _setup(tmp_path)
        checkpoint = service.create_checkpoint(timestamp_ns=5000)
        bad = type(checkpoint)(**{**checkpoint.__dict__, "origin_signature": "00" * 64})
        with pytest.raises(WitnessError, match="Origin signature invalid"):
            validate_quorum(
                bad,
                min_witnesses=0,
                trusted_fingerprints=trusted,
                verify=backend.verify,
            )


class TestInclusionProofs:
    def test_prove_and_verify_entry(self, tmp_path: Path):
        service, *_ = _setup(tmp_path)
        checkpoint = service.create_checkpoint(timestamp_ns=5000)
        proof = service.prove_entry(2)
        assert proof.entry_index == 2
        assert WitnessService.verify_entry_inclusion(proof, checkpoint)

    def test_tampered_proof_rejected(self, tmp_path: Path):
        service, *_ = _setup(tmp_path)
        checkpoint = service.create_checkpoint(timestamp_ns=5000)
        proof = service.prove_entry(2)
        tampered = type(proof)(
            checkpoint_id=proof.checkpoint_id,
            entry_index=proof.entry_index,
            entry_hash="f" * 64,
            inclusion_proof=proof.inclusion_proof,
        )
        assert not WitnessService.verify_entry_inclusion(tampered, checkpoint)

    def test_wrong_checkpoint_rejected(self, tmp_path: Path):
        service, *_ = _setup(tmp_path)
        service.create_checkpoint(timestamp_ns=5000)
        proof = service.prove_entry(1)
        # Second checkpoint covers one more entry (the first checkpoint line).
        later = service.create_checkpoint(timestamp_ns=6000)
        assert proof.checkpoint_id != later.checkpoint_id
        assert not WitnessService.verify_entry_inclusion(proof, later)

    def test_out_of_range_rejected(self, tmp_path: Path):
        service, *_ = _setup(tmp_path)
        service.create_checkpoint(timestamp_ns=5000)
        with pytest.raises(WitnessError, match="outside checkpointed range"):
            service.prove_entry(99)

    def test_no_checkpoints_rejected(self, tmp_path: Path):
        service, *_ = _setup(tmp_path)
        with pytest.raises(WitnessError, match="No checkpoints"):
            service.prove_entry(0)
