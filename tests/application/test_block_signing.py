"""Tests for SEL-X block signing, CausalVector single-sourcing, and the
block-chain verification tool (scripts/verify_block_chain.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from egregore.application.block_builder import BlockCommitPolicy, ExecutionBlockBuilder
from egregore.domain.causal_vector import CausalVector as CanonicalCausalVector
from egregore.domain.execution_block import CausalVector as ReexportedCausalVector
from egregore.domain.execution_block import ExecutionBlock
from egregore.domain.execution_record import (
    ExecutionRecord,
    PolicyContext,
    generate_record_id,
)
from egregore.infrastructure.block_store import BlockStore
from egregore.kernel.ed25519_signer import (
    generate_signing_key,
    get_verify_key_hex,
    sign_message,
    verify_message,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_block_chain", REPO_ROOT / "scripts" / "verify_block_chain.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("verify_block_chain", module)
    spec.loader.exec_module(module)
    return module


def _record(seq: int, trace_id: str = "tr") -> ExecutionRecord:
    return ExecutionRecord(
        record_id=generate_record_id(trace_id=trace_id, timestamp_ns=seq, operation="op"),
        timestamp_ns=seq,
        tenant_id="t",
        principal_id="u",
        role="admin",
        session_id="s",
        trace_id=trace_id,
        subsystem="sub",
        operation="op",
        policy_context=PolicyContext(policy_version="v1", engine_version="v1"),
    ).with_integrity_hash()


def _signing_builder(signing_key_hex: str) -> ExecutionBlockBuilder:
    return ExecutionBlockBuilder(
        commit_policy=BlockCommitPolicy(max_records=1, max_age_ns=10**12),
        now_ns=lambda: 10**12,
        signer=lambda digest: sign_message(
            signing_key_hex=signing_key_hex, message=digest.encode("utf-8")
        ),
    )


class TestCausalVectorSingleSource:
    def test_execution_block_reexports_canonical_causal_vector(self) -> None:
        assert ReexportedCausalVector is CanonicalCausalVector

    def test_block_default_vector_is_canonical_type(self) -> None:
        block = ExecutionBlock()
        assert type(block.causal_vector) is CanonicalCausalVector

    def test_block_accepts_dict_causal_vector(self) -> None:
        block = ExecutionBlock(causal_vector={"vector": {"n": 1}, "trace_id": "x"})
        assert isinstance(block.causal_vector, CanonicalCausalVector)
        assert block.causal_vector.vector == {"n": 1}


class TestBlockSigning:
    def test_builder_signs_when_signer_injected(self) -> None:
        sk = generate_signing_key()
        block = _signing_builder(sk).append(_record(1))
        assert block is not None
        assert block.block_signature
        assert verify_message(
            verify_key_hex=get_verify_key_hex(sk),
            message=block.integrity_hash.encode("utf-8"),
            signature_hex=block.block_signature,
        )

    def test_unsigned_builder_emits_empty_signature(self) -> None:
        builder = ExecutionBlockBuilder(
            commit_policy=BlockCommitPolicy(max_records=1, max_age_ns=10**12),
            now_ns=lambda: 10**12,
        )
        block = builder.append(_record(1))
        assert block is not None
        assert block.block_signature == ""

    def test_tampered_record_changes_merkle_and_integrity(self) -> None:
        sk = generate_signing_key()
        block = _signing_builder(sk).append(_record(1))
        assert block is not None
        tampered = ExecutionBlock(
            **{**block.__dict__, "records": (_record(2),)}
        ).with_integrity_hash()
        assert tampered.merkle_root == block.merkle_root  # merkle set at build
        assert tampered.integrity_hash != block.integrity_hash
        assert not verify_message(
            verify_key_hex=get_verify_key_hex(sk),
            message=tampered.integrity_hash.encode("utf-8"),
            signature_hex=block.block_signature,
        )

    def test_chain_links_via_integrity_hash(self) -> None:
        sk = generate_signing_key()
        builder = _signing_builder(sk)
        first = builder.append(_record(1))
        second = builder.append(_record(2))
        assert first is not None and second is not None
        assert second.previous_block_hash == first.integrity_hash


class TestChainVerifier:
    def _write_chain(self, tmp_path: Path, signing: bool = True) -> tuple[Path, str]:
        sk = generate_signing_key()
        builder = (
            _signing_builder(sk)
            if signing
            else ExecutionBlockBuilder(
                commit_policy=BlockCommitPolicy(max_records=1, max_age_ns=10**12),
                now_ns=lambda: 10**12,
            )
        )
        store = BlockStore(tmp_path / "blocks.zarc")
        for seq in (1, 2, 3):
            block = builder.append(_record(seq))
            assert block is not None
            store.append(block)
        return tmp_path / "blocks.zarc", sk

    def test_signed_chain_verifies(self, tmp_path: Path, capsys) -> None:
        verifier = _load_verifier()
        path, sk = self._write_chain(tmp_path)
        assert verifier.verify_chain(path, get_verify_key_hex(sk)) == 0
        assert "no violations" in capsys.readouterr().out

    def test_unsigned_chain_verifies_without_key(self, tmp_path: Path) -> None:
        verifier = _load_verifier()
        path, _ = self._write_chain(tmp_path, signing=False)
        assert verifier.verify_chain(path, None) == 0

    def test_signed_chain_requires_key(self, tmp_path: Path) -> None:
        verifier = _load_verifier()
        path, _ = self._write_chain(tmp_path)
        assert verifier.verify_chain(path, None) == 1

    def test_wrong_key_fails(self, tmp_path: Path) -> None:
        verifier = _load_verifier()
        path, _ = self._write_chain(tmp_path)
        other = generate_signing_key()
        assert verifier.verify_chain(path, get_verify_key_hex(other)) == 1

    def test_chain_break_detected(self, tmp_path: Path) -> None:
        verifier = _load_verifier()
        path, sk = self._write_chain(tmp_path)
        lines = path.read_text().splitlines()
        # Corrupt the second block's previous_block_hash.
        import json as _json  # local alias to avoid confusion with canonical

        obj = _json.loads(lines[1])
        obj["previous_block_hash"] = "f" * 64
        lines[1] = _json.dumps(obj)
        path.write_text("\n".join(lines) + "\n")
        assert verifier.verify_chain(path, get_verify_key_hex(sk)) == 1

    def test_tampered_record_detected(self, tmp_path: Path) -> None:
        verifier = _load_verifier()
        path, sk = self._write_chain(tmp_path)
        lines = path.read_text().splitlines()
        import json as _json

        obj = _json.loads(lines[0])
        obj["records"][0]["payload"] = {"injected": True}
        lines[0] = _json.dumps(obj)
        path.write_text("\n".join(lines) + "\n")
        assert verifier.verify_chain(path, get_verify_key_hex(sk)) == 1

    def test_missing_file_fails_closed(self, tmp_path: Path) -> None:
        verifier = _load_verifier()
        assert verifier.verify_chain(tmp_path / "absent.zarc", None) == 2


@pytest.fixture(autouse=True)
def _isolate_sys_modules():
    yield
    sys.modules.pop("verify_block_chain", None)
