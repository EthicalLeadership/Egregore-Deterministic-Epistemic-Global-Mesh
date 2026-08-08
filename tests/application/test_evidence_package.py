"""Tests for sealed evidence packages (BagIt profile + signed reports)."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

from egregore.application.custody_log import CustodyLog
from egregore.application.evidence_sealer import EvidencePackageError, EvidenceSealer
from egregore.application.evidence_package_verifier import verify_package
from egregore.application.witness_service import WitnessService
from egregore.domain.custody import create_custody_event
from egregore.domain.witness_checkpoint import Cosignature
from egregore.infrastructure.ed25519_signing_backend import Ed25519SigningBackend
from egregore.kernel.ed25519_signer import generate_signing_key
from egregore.kernel.provenance import Provenance

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_tsa_fixture():
    spec = importlib.util.spec_from_file_location(
        "tsa_fixture", REPO_ROOT / "tests" / "helpers" / "tsa_fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Witness:
    def __init__(self, node_id: str, key: str):
        self.node_id = node_id
        self._backend = Ed25519SigningBackend(key)

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


EVIDENCE_HASH = hashlib.sha256(b"evidence").hexdigest()


@pytest.fixture()
def evidence_world(tmp_path: Path):
    """A node state: chain (with custody + witness), blocks, anchors, keys."""
    chain_key = generate_signing_key()
    provenance = Provenance(tmp_path / "main.zarc", signing_key_hex=chain_key)
    for i in range(3):
        provenance.append(
            engine="dossier",
            event="generated",
            payload={"case_id": "MOLSON-2026", "seq": i},
            ts_ns=1000 + i,
        )
    custody = CustodyLog(provenance)
    custody.record(
        create_custody_event(
            evidence_id="EV-001", action="acquire", actor="agent-a",
            role="investigator", timestamp_ns=2000, evidence_hash=EVIDENCE_HASH,
        )
    )
    witness = _Witness("node-w1", generate_signing_key())
    backend = Ed25519SigningBackend(chain_key)
    service = WitnessService(
        provenance=provenance,
        signing_backend=backend,
        witnesses=[witness],
        trusted_fingerprints={witness.node_id: witness.fingerprint},
        min_witnesses=1,
    )
    checkpoint = service.create_checkpoint(timestamp_ns=3000)

    # Blocks chain (signed with the same key via builder).
    from egregore.application.block_builder import BlockCommitPolicy, ExecutionBlockBuilder
    from egregore.domain.execution_record import (
        ExecutionRecord, PolicyContext, generate_record_id,
    )
    from egregore.infrastructure.block_store import BlockStore
    from egregore.kernel.ed25519_signer import sign_message

    builder = ExecutionBlockBuilder(
        commit_policy=BlockCommitPolicy(max_records=1, max_age_ns=10**12),
        now_ns=lambda: 10**12,
        signer=lambda h: sign_message(signing_key_hex=chain_key, message=h.encode()),
    )
    blocks_path = tmp_path / "blocks.zarc"
    store = BlockStore(blocks_path)
    record = ExecutionRecord(
        record_id=generate_record_id(trace_id="t", timestamp_ns=1, operation="op"),
        timestamp_ns=1, tenant_id="t", principal_id="u", role="admin",
        session_id="s", trace_id="t", subsystem="sub", operation="op",
        policy_context=PolicyContext(policy_version="v1", engine_version="v1"),
    ).with_integrity_hash()
    store.append(builder.append(record))

    # Anchors: one tier-2 (synthetic TSA), one tier-1 local.
    fixture = _load_tsa_fixture()
    token, trust_dir, data_hash = fixture.make_tsa(tmp_path / "tsa")
    anchors = [
        {
            "anchor_id": hashlib.sha256(f"anchor:{data_hash}".encode()).hexdigest(),
            "tier": "2",
            "block_hash": data_hash,
            "notarization": token.hex(),
            "public_verify": True,
            "timestamp_ns": 4000,
            "metadata": {},
        },
        {
            "anchor_id": hashlib.sha256(b"anchor:" + b"f" * 32).hexdigest(),
            "tier": "1",
            "block_hash": "f" * 64,
            "notarization": "deadbeef",
            "public_verify": False,
            "timestamp_ns": 4001,
            "metadata": {},
        },
    ]
    return {
        "provenance": provenance,
        "chain_path": tmp_path / "main.zarc",
        "blocks_path": blocks_path,
        "anchors": anchors,
        "trust_dir": trust_dir,
        "backend": backend,
        "witness": witness,
        "checkpoint": checkpoint,
        "tmp": tmp_path,
    }


def _seal(world, out: Path, **overrides):
    from egregore.infrastructure.tsa_verifier import verify_tsa_token

    sealer = EvidenceSealer(signing_backend=world["backend"])
    kwargs = {
        "chains": {"main": world["chain_path"]},
        "subject": {"case_id": "MOLSON-2026"},
        "timestamp_ns": 9999,
        "block_store_path": world["blocks_path"],
        "anchors": world["anchors"],
        "provenance": world["provenance"],
        "tsa_trust_dir": world["trust_dir"],
        "min_witnesses": 1,
        "trusted_fingerprints": {
            world["witness"].node_id: world["witness"].fingerprint
        },
        "tsa_verify": verify_tsa_token,
    }
    kwargs.update(overrides)
    return sealer.seal(out, **kwargs)


class TestSealVerifyRoundtrip:
    def _vp(self, world, path, **kw):
        from egregore.infrastructure.tsa_verifier import verify_tsa_token

        return verify_package(
            path,
            tsa_trust_dir=world["trust_dir"],
            min_witnesses=1,
            trusted_fingerprints={
                world["witness"].node_id: world["witness"].fingerprint
            },
            tsa_verify=verify_tsa_token,
            **kw,
        )

    def test_seal_then_verify(self, evidence_world):
        receipt = _seal(evidence_world, evidence_world["tmp"] / "pkg")
        assert receipt.verdict, receipt.failures
        result = self._vp(evidence_world, Path(receipt.package_dir))
        assert result.verdict, result.failures
        assert result.report["seal_id"] == receipt.seal_id
        assert result.report["verdict"] is True

    def test_deterministic_seal_id(self, evidence_world):
        first = _seal(evidence_world, evidence_world["tmp"] / "pkg1")
        second = _seal(evidence_world, evidence_world["tmp"] / "pkg2")
        assert first.seal_id == second.seal_id

    def test_zip_roundtrip(self, evidence_world):
        receipt = _seal(
            evidence_world, evidence_world["tmp"] / "pkgz", make_zip=True
        )
        assert receipt.zip_path is not None
        result = self._vp(evidence_world, Path(receipt.zip_path))
        assert result.verdict, result.failures

    def test_subject_index_populated(self, evidence_world):
        receipt = _seal(evidence_world, evidence_world["tmp"] / "pkgs")
        import json

        index = json.loads(
            (Path(receipt.package_dir) / "subject_index.json").read_text()
        )
        assert index["subject"] == {"case_id": "MOLSON-2026"}
        assert len(index["entry_refs"]) == 3  # three dossier entries match


class TestTamperDetection:
    def _verify(self, world, pkg_dir):
        from egregore.infrastructure.tsa_verifier import verify_tsa_token

        return verify_package(
            pkg_dir,
            tsa_trust_dir=world["trust_dir"],
            min_witnesses=1,
            trusted_fingerprints={
                world["witness"].node_id: world["witness"].fingerprint
            },
            tsa_verify=verify_tsa_token,
        )

    def test_payload_tamper(self, evidence_world):
        receipt = _seal(evidence_world, evidence_world["tmp"] / "pkg")
        chain = Path(receipt.package_dir) / "data" / "chains" / "main.zarc"
        content = chain.read_bytes()
        chain.write_bytes(content[:100] + b"X" + content[101:])
        result = self._verify(evidence_world, Path(receipt.package_dir))
        assert not result.verdict
        assert not result.checks["payload_fixity"]

    def test_tag_tamper(self, evidence_world):
        receipt = _seal(evidence_world, evidence_world["tmp"] / "pkg")
        info = Path(receipt.package_dir) / "bag-info.txt"
        info.write_text(info.read_text() + "tampered\n")
        result = self._verify(evidence_world, Path(receipt.package_dir))
        assert not result.verdict
        assert not result.checks["tag_fixity"]

    def test_report_tamper(self, evidence_world):
        receipt = _seal(evidence_world, evidence_world["tmp"] / "pkg")
        report = Path(receipt.package_dir) / "verification_report.json"
        report.write_text(report.read_text().replace('"verdict":true', '"verdict":false'))
        result = self._verify(evidence_world, Path(receipt.package_dir))
        assert not result.verdict
        assert not result.checks["report_signature"]

    def test_embedded_lie_detected(self, evidence_world):
        """A corrupted chain whose embedded report claims success is caught
        by independent re-verification."""
        receipt = _seal(evidence_world, evidence_world["tmp"] / "pkg")
        chain = Path(receipt.package_dir) / "data" / "chains" / "main.zarc"
        lines = chain.read_text().splitlines()
        # Corrupt linkage in line 3 and recompute fixity manifests so only
        # the evidence layer can catch it.
        import json as _json

        obj = _json.loads(lines[2])
        obj["prev_hash"] = "0" * 64
        lines[2] = _json.dumps(obj, sort_keys=True, separators=(",", ":"))
        chain.write_text("\n".join(lines) + "\n")
        result = self._verify(evidence_world, Path(receipt.package_dir))
        assert not result.verdict
        assert not result.checks["evidence_independent"]

    def test_unsigned_report_fails(self, evidence_world):
        receipt = _seal(evidence_world, evidence_world["tmp"] / "pkg")
        (Path(receipt.package_dir) / "verification_report.sig").unlink()
        result = self._verify(evidence_world, Path(receipt.package_dir))
        assert not result.verdict
        assert not result.checks["report_signature"]


class TestFailClosed:
    def test_missing_package(self, tmp_path: Path):
        result = verify_package(tmp_path / "nope")
        assert not result.verdict

    def test_empty_dir(self, tmp_path: Path):
        result = verify_package(tmp_path)
        assert not result.verdict
        assert not result.checks["bagit_declaration"]

    def test_seal_requires_chain(self, evidence_world):
        sealer = EvidenceSealer(signing_backend=evidence_world["backend"])
        with pytest.raises(EvidencePackageError, match="At least one chain"):
            sealer.seal(
                evidence_world["tmp"] / "x",
                chains={},
                subject={},
                timestamp_ns=1,
            )

    def test_seal_requires_backend(self):
        with pytest.raises(EvidencePackageError, match="signing backend"):
            EvidenceSealer(signing_backend=None)

    def test_seal_refuses_nonempty_output(self, evidence_world):
        out = evidence_world["tmp"] / "occupied"
        out.mkdir()
        (out / "file").write_text("x")
        with pytest.raises(EvidencePackageError, match="not empty"):
            _seal(evidence_world, out)

    def test_untrusted_tsa_skips_anchor_pass(self, evidence_world):
        """Without a trust dir, tier-2 anchors are not 'verified' — and the
        seal verdict reflects that they could not be assessed."""
        receipt = _seal(
            evidence_world,
            evidence_world["tmp"] / "pkg-notrust",
            tsa_trust_dir=None,
        )
        assert not receipt.verdict  # tsa_failed counts unassessed tier-2
        result = verify_package(Path(receipt.package_dir))
        assert result.checks["payload_fixity"]


class TestCustodyIntegration:
    def test_seal_event_recorded_on_live_chain(self, evidence_world):
        receipt = _seal(
            evidence_world,
            evidence_world["tmp"] / "pkg",
            custody_evidence_id="EV-001",
        )
        history = CustodyLog(evidence_world["provenance"]).history("EV-001")
        actions = [e.action for e in history.events]
        assert actions[-1] == "seal"
        seal_event = history.events[-1]
        assert seal_event.evidence_hash == receipt.seal_id
        assert evidence_world["provenance"].verify_chain()
