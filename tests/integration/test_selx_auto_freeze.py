"""SEL-X auto-freeze integration test: corrupt block hash triggers freeze."""

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
    "BLACKSTAR_DSN", "postgresql://egregore:egregore@localhost:5432/egregore"
)


@pytest.fixture
def tenant_id():
    return f"freeze_test_{uuid.uuid4().hex[:8]}"


def _make_record(tenant_id: str) -> ExecutionRecord:
    return ExecutionRecord(
        record_id=f"rec-{uuid.uuid4().hex}",
        timestamp_ns=time.time_ns(),
        tenant_id=tenant_id,
        principal_id="test_principal",
        role="test_role",
        session_id="test_session",
        trace_id="test_trace",
        subsystem="test",
        operation="freeze_test",
        policy_context=PolicyContext(
            policy_version="policy_v1",
            engine_version="engine_v1",
        ),
        input_hash="a" * 64,
        output_hash="b" * 64,
        payload={"test": True},
        success=True,
    )


@pytest.mark.asyncio
async def test_corrupt_block_triggers_freeze(tenant_id: str):
    store = PostgresBlockStore(DSN)

    builder = ExecutionBlockBuilder(
        node_id="test_node", signer=lambda h: f"sig_{h[:16]}"
    )
    record = _make_record(tenant_id)
    builder.append(record)
    block = builder.flush()
    assert block is not None
    store.append(block)

    # Corrupt the stored block hash.
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE execution_blocks SET block_hash = 'FAKE' WHERE tenant_id = %s",
            (tenant_id,),
        )
        conn.commit()
    finally:
        conn.close()

    fc = FreezeController()
    watcher = IntegrityWatcher(
        store,
        fc,
        tenant_id=tenant_id,
        interval_sec=0.5,
    )
    await watcher.start()

    try:
        # Wait for the watcher to poll and detect the corruption.
        for _ in range(20):
            if fc.is_frozen:
                break
            await asyncio.sleep(0.1)

        assert fc.is_frozen, "FreezeController should have transitioned to FROZEN"
        assert len(fc.history) >= 1
        event = fc.history[-1]
        assert "mismatch" in event.reason.lower() or " Integrity" in event.reason
        print(f"Freeze triggered: {event.reason}")
    finally:
        await watcher.stop()
