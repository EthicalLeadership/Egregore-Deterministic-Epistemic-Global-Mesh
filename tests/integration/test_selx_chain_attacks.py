"""SEL-X chain-attack integration tests: insert, delete, reorder.

These tests verify that IntegrityWatcher detects chain-level tampering,
not just individual block corruption.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from egregore.application.block_builder import ExecutionBlockBuilder
from egregore.application.integrity_watcher import IntegrityWatcher
from egregore.domain.execution_record import ExecutionRecord, PolicyContext
from egregore.infrastructure.postgres_block_store import PostgresBlockStore
from egregore.shared.freeze_state import FreezeController

DSN = os.environ.get(
    "EGREGORE_DSN", "postgresql://egregore:egregore@localhost:5432/egregore"
)


@pytest.fixture
def tenant_id():
    return f"chain_attack_{uuid.uuid4().hex[:8]}"


def _make_record(tenant_id: str, seq: int = 0) -> ExecutionRecord:
    return ExecutionRecord(
        record_id=f"rec-{uuid.uuid4().hex}",
        timestamp_ns=time.time_ns() + seq,
        tenant_id=tenant_id,
        principal_id="test_principal",
        role="test_role",
        session_id="test_session",
        trace_id="test_trace",
        subsystem="test",
        operation="chain_attack_test",
        policy_context=PolicyContext(
            policy_version="policy_v1",
            engine_version="engine_v1",
        ),
        input_hash="a" * 64,
        output_hash="b" * 64,
        payload={"test": True, "seq": seq},
        success=True,
    )


def _seed_chain(store: PostgresBlockStore, tenant_id: str, n_blocks: int = 5):
    """Build and persist a valid chain of N blocks."""
    builder = ExecutionBlockBuilder(
        node_id="test_node",
        signer=lambda h: f"sig_{h[:16]}",
    )
    blocks = []
    for i in range(n_blocks):
        record = _make_record(tenant_id, seq=i)
        builder.append(record)
        block = builder.flush()
        assert block is not None
        store.append(block)
        blocks.append(block)
    return blocks


def _raw_execute(sql: str, params: tuple):
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_inserted_block_triggers_freeze(tenant_id: str):
    store = PostgresBlockStore(DSN)
    _seed_chain(store, tenant_id, n_blocks=5)

    # Attack: insert a fake block at height 3 with wrong previous_hash.
    fake_block_id = f"evil-{uuid.uuid4().hex}"
    _raw_execute(
        """
        INSERT INTO execution_blocks
        (block_id, tenant_id, block_seq, block_height, previous_block_hash,
         merkle_root, record_count, block_hash, block_signature, causal_vector, records, created_at_ns)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            fake_block_id,
            tenant_id,
            999,
            3,
            "EVIL_PREVIOUS_HASH_" + "0" * 40,
            "evil_merkle",
            0,
            "EVIL_BLOCK_HASH_" + "0" * 48,
            "evil_sig",
            "{}",
            "[]",
            time.time_ns(),
        ),
    )

    fc = FreezeController(tenant_id=tenant_id)
    watcher = IntegrityWatcher(
        store,
        fc,
        tenant_id=tenant_id,
        interval_sec=0.5,
    )
    await watcher.start()

    try:
        for _ in range(20):
            if fc.is_frozen:
                break
            await asyncio.sleep(0.1)

        assert fc.is_frozen, "FreezeController should have transitioned to FROZEN"
        event = fc.history[-1]
        assert any(
            keyword in event.reason
            for keyword in ("CHAIN_BREAK", "HEIGHT_GAP", "HASH_MISMATCH")
        ), f"Unexpected reason: {event.reason}"
        print(f"INSERT attack detected: {event.reason}")
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_deleted_block_triggers_freeze(tenant_id: str):
    store = PostgresBlockStore(DSN)
    _seed_chain(store, tenant_id, n_blocks=5)

    # Attack: delete the block at height 2, creating a gap.
    _raw_execute(
        "DELETE FROM execution_blocks WHERE tenant_id = %s AND block_height = %s",
        (tenant_id, 2),
    )

    fc = FreezeController(tenant_id=tenant_id)
    watcher = IntegrityWatcher(
        store,
        fc,
        tenant_id=tenant_id,
        interval_sec=0.5,
    )
    await watcher.start()

    try:
        for _ in range(20):
            if fc.is_frozen:
                break
            await asyncio.sleep(0.1)

        assert fc.is_frozen, "FreezeController should have transitioned to FROZEN"
        event = fc.history[-1]
        assert any(
            keyword in event.reason for keyword in ("HEIGHT_GAP", "CHAIN_BREAK")
        ), f"Unexpected reason: {event.reason}"
        print(f"DELETE attack detected: {event.reason}")
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_reordered_blocks_trigger_freeze(tenant_id: str):
    store = PostgresBlockStore(DSN)
    blocks = _seed_chain(store, tenant_id, n_blocks=5)

    # Attack: swap the block_hash values of height 2 and height 3.
    # This preserves heights but breaks previous_hash linkage.
    hash_2 = blocks[2].integrity_hash
    hash_3 = blocks[3].integrity_hash
    _raw_execute(
        "UPDATE execution_blocks SET block_hash = %s WHERE tenant_id = %s AND block_height = %s",
        (hash_3, tenant_id, 2),
    )
    _raw_execute(
        "UPDATE execution_blocks SET block_hash = %s WHERE tenant_id = %s AND block_height = %s",
        (hash_2, tenant_id, 3),
    )

    fc = FreezeController(tenant_id=tenant_id)
    watcher = IntegrityWatcher(
        store,
        fc,
        tenant_id=tenant_id,
        interval_sec=0.5,
    )
    await watcher.start()

    try:
        for _ in range(20):
            if fc.is_frozen:
                break
            await asyncio.sleep(0.1)

        assert fc.is_frozen, "FreezeController should have transitioned to FROZEN"
        event = fc.history[-1]
        assert any(
            keyword in event.reason for keyword in ("CHAIN_BREAK", "HASH_MISMATCH")
        ), f"Unexpected reason: {event.reason}"
        print(f"REORDER attack detected: {event.reason}")
    finally:
        await watcher.stop()
