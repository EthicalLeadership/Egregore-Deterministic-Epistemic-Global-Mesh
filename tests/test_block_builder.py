"""Tests for the SEL-X ExecutionBlockBuilder."""

from __future__ import annotations

from egregore.application.block_builder import BlockCommitPolicy, ExecutionBlockBuilder
from egregore.domain.execution_record import (
    ExecutionRecord,
    PolicyContext,
    generate_record_id,
)


def _record(seq: int) -> ExecutionRecord:
    return ExecutionRecord(
        record_id=generate_record_id(trace_id="tr", timestamp_ns=seq, operation="op"),
        timestamp_ns=seq,
        tenant_id="t",
        principal_id="u",
        role="admin",
        session_id="s",
        trace_id="tr",
        subsystem="sub",
        operation="op",
        policy_context=PolicyContext(policy_version="v1", engine_version="v1"),
    ).with_integrity_hash()


def test_builder_flushes_at_max_records() -> None:
    clock = [0]

    def now_ns():
        clock[0] += 1
        return clock[0]

    builder = ExecutionBlockBuilder(
        commit_policy=BlockCommitPolicy(max_records=2, max_age_ns=1_000_000),
        now_ns=now_ns,
    )
    block = builder.append(_record(1))
    assert block is None
    block = builder.append(_record(2))
    assert block is not None
    assert block.block_seq == 0
    assert len(block.records) == 2
    assert block.merkle_root is not None


def test_builder_build_all_respects_max_records() -> None:
    builder = ExecutionBlockBuilder(
        commit_policy=BlockCommitPolicy(max_records=3, max_age_ns=1_000_000),
    )
    records = [_record(i) for i in range(1, 8)]
    blocks = builder.build_all(records)
    assert len(blocks) == 3
    assert len(blocks[0].records) == 3
    assert len(blocks[1].records) == 3
    assert len(blocks[2].records) == 1


def test_blocks_chain_previous_hash() -> None:
    builder = ExecutionBlockBuilder(
        commit_policy=BlockCommitPolicy(max_records=1, max_age_ns=1_000_000),
    )
    block1 = builder.append(_record(1))
    assert block1 is not None
    block2 = builder.append(_record(2))
    assert block2 is not None
    assert block2.previous_block_hash == block1.integrity_hash


def test_empty_flush_returns_none() -> None:
    builder = ExecutionBlockBuilder()
    assert builder.flush() is None


def test_block_ids_are_deterministic_for_same_input() -> None:
    builder1 = ExecutionBlockBuilder(
        commit_policy=BlockCommitPolicy(max_records=1, max_age_ns=1_000_000),
        now_ns=lambda: 1000,
    )
    builder2 = ExecutionBlockBuilder(
        commit_policy=BlockCommitPolicy(max_records=1, max_age_ns=1_000_000),
        now_ns=lambda: 1000,
    )
    r1 = _record(1)
    block1 = builder1.append(r1)
    block2 = builder2.append(r1)
    assert block1 is not None
    assert block2 is not None
    assert block1.block_id == block2.block_id
