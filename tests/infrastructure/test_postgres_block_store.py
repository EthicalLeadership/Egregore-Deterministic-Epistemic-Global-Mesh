"""Tests for PostgreSQL-backed block store."""

from __future__ import annotations

import pytest

testing_postgresql = pytest.importorskip("testing.postgresql")

from egregore.domain.execution_block import CausalVector, ExecutionBlock
from egregore.domain.execution_record import (
    BudgetContext,
    ExecutionRecord,
    PolicyContext,
)
from egregore.infrastructure.postgres_block_store import PostgresBlockStore


def _record(record_id: str = "r1", tenant_id: str = "t1") -> ExecutionRecord:
    return ExecutionRecord(
        record_id=record_id,
        timestamp_ns=1,
        tenant_id=tenant_id,
        principal_id="u",
        role="admin",
        session_id="s",
        trace_id="tr",
        subsystem="sub",
        operation="op",
        policy_context=PolicyContext(policy_version="v1", engine_version="v1"),
        budget_context=BudgetContext(
            budget_id="b1", pre_balance=100, post_balance=95, cost_units=5
        ),
    ).with_integrity_hash()


def _block(tenant_id: str = "t1", seq: int = 0) -> ExecutionBlock:
    return ExecutionBlock(
        block_id=f"b{seq}",
        block_seq=seq,
        created_at_ns=seq + 1,
        records=(_record(tenant_id=tenant_id),),
        merkle_root="m" * 64,
        previous_block_hash="0" * 64 if seq == 0 else "p" * 64,
        causal_vector=CausalVector(),
    ).with_integrity_hash()


@pytest.fixture(scope="function")
def pg_store():
    with testing_postgresql.Postgresql() as postgresql:
        store = PostgresBlockStore(postgresql.url())
        yield store


def test_append_and_read_block(pg_store: PostgresBlockStore) -> None:
    block = _block()
    pg_store.append(block)
    blocks = pg_store.read_all("t1")
    assert len(blocks) == 1
    assert blocks[0].block_id == block.block_id
    assert blocks[0].integrity_hash == block.integrity_hash
    assert blocks[0].records[0].tenant_id == "t1"


def test_get_latest_height(pg_store: PostgresBlockStore) -> None:
    assert pg_store.get_latest_height("t1") == -1
    pg_store.append(_block(seq=0))
    assert pg_store.get_latest_height("t1") == 0
    pg_store.append(_block(seq=1))
    assert pg_store.get_latest_height("t1") == 1


def test_get_latest_block_hash(pg_store: PostgresBlockStore) -> None:
    block = _block(seq=0)
    pg_store.append(block)
    assert pg_store.get_latest_block_hash("t1") == block.integrity_hash


def test_tenant_isolation(pg_store: PostgresBlockStore) -> None:
    pg_store.append(_block(tenant_id="t1", seq=0))
    pg_store.append(_block(tenant_id="t2", seq=1))
    assert len(pg_store.read_all("t1")) == 1
    assert len(pg_store.read_all("t2")) == 1


def test_idempotent_append(pg_store: PostgresBlockStore) -> None:
    block = _block(seq=0)
    pg_store.append(block)
    pg_store.append(block)
    assert len(pg_store.read_all("t1")) == 1
