"""SEL-X full pipeline integration test: block -> Postgres -> RFC 3161 anchor."""

from __future__ import annotations

import os
import time
import uuid

import pytest

pytest.importorskip("psycopg2")

from egregore.application.block_builder import ExecutionBlockBuilder
from egregore.domain.execution_record import ExecutionRecord, PolicyContext
from egregore.infrastructure.postgres_block_store import PostgresBlockStore
from egregore.services.anchor_orchestrator.service import AnchorOrchestrator
from egregore.services.anchor_orchestrator.timestamp_client import (
    RFC3161TimestampClient,
)

DSN = os.environ.get(
    "BLACKSTAR_DSN", "postgresql://egregore:egregore@localhost:5432/egregore"
)


@pytest.fixture
def tenant_id():
    return f"test_tenant_{uuid.uuid4().hex[:8]}"


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
        operation="anchor_pipeline_test",
        policy_context=PolicyContext(
            policy_version="policy_v1",
            engine_version="engine_v1",
        ),
        input_hash="a" * 64,
        output_hash="b" * 64,
        payload={"test": True},
        success=True,
    )


def test_block_to_anchor_pipeline(tenant_id: str):
    store = PostgresBlockStore(DSN)

    builder = ExecutionBlockBuilder(
        node_id="test_node", signer=lambda h: f"sig_{h[:16]}"
    )
    record = _make_record(tenant_id)
    builder.append(record)
    block = builder.flush()
    assert block is not None, "Block builder should have flushed the record"

    store.append(block)
    latest = store.get_latest_block_hash(tenant_id)
    assert latest == block.integrity_hash

    # Anchor via orchestrator (uses RFC3161TimestampClient by default when TSA URL is set)
    orchestrator = AnchorOrchestrator.from_dsn(
        block_store_path=None,  # not used when from_dsn builds PostgresBlockStore
        dsn=DSN,
        tsa_url="https://freetsa.org/tsr",
        signing_key_hex="01" * 32,
        tier="tsa",
    )

    record = orchestrator.anchor_block(block.integrity_hash)

    # Tier reflects the actual token source: 2 for RFC 3161, 1 for local fallback.
    assert record.tier in ("1", "2"), f"Unexpected tier: {record.tier}"
    assert record.block_hash == block.integrity_hash
    assert record.notarization
    assert record.timestamp_ns > 0
    print(
        f"Anchored block {block.integrity_hash} with tier={record.tier} at {record.timestamp_ns}"
    )


def test_rfc3161_client_directly():
    """Sanity check that the timestamp client returns a TimestampToken."""
    client = RFC3161TimestampClient()
    token = client.timestamp("a" * 64)
    assert token.tier in (1, 2)
    assert token.timestamp_iso
    assert token.cms_bytes
    print(
        f"TIER: {token.tier}, BYTES: {len(token.cms_bytes)}, TIME: {token.timestamp_iso}"
    )
