"""Tests for the SEL-X ExecutionBlock schema."""

from __future__ import annotations

from egregore.domain.execution_block import (
    CausalVector,
    ExecutionBlock,
    generate_block_id,
)
from egregore.domain.execution_record import ExecutionRecord, PolicyContext


def _record(
    record_id: str = "r1", integrity_hash: str | None = None
) -> ExecutionRecord:
    return ExecutionRecord(
        record_id=record_id,
        timestamp_ns=1,
        tenant_id="t",
        principal_id="u",
        role="admin",
        session_id="s",
        trace_id="tr",
        subsystem="sub",
        operation="op",
        policy_context=PolicyContext(policy_version="v1", engine_version="v1"),
        integrity_hash=integrity_hash or ("h" + record_id),
    )


def test_block_id_is_deterministic() -> None:
    bid1 = generate_block_id(
        block_seq=0, merkle_root="root", previous_block_hash="0" * 64, timestamp_ns=100
    )
    bid2 = generate_block_id(
        block_seq=0, merkle_root="root", previous_block_hash="0" * 64, timestamp_ns=100
    )
    assert bid1 == bid2


def test_block_integrity_hash_covers_records() -> None:
    block = ExecutionBlock(
        block_id="b1",
        block_seq=0,
        created_at_ns=1,
        records=(_record("r1"), _record("r2")),
        merkle_root="root",
        previous_block_hash="0" * 64,
        causal_vector=CausalVector(),
    ).with_integrity_hash()
    assert block.integrity_hash is not None
    assert len(block.integrity_hash) == 64


def test_block_integrity_changes_when_record_changes() -> None:
    block1 = ExecutionBlock(
        block_id="b1",
        block_seq=0,
        created_at_ns=1,
        records=(_record("r1", "hash1"),),
        merkle_root="root",
        previous_block_hash="0" * 64,
        causal_vector=CausalVector(),
    ).with_integrity_hash()

    block2 = ExecutionBlock(
        block_id="b1",
        block_seq=0,
        created_at_ns=1,
        records=(_record("r1", "hash2"),),
        merkle_root="root",
        previous_block_hash="0" * 64,
        causal_vector=CausalVector(),
    ).with_integrity_hash()

    assert block1.integrity_hash != block2.integrity_hash
