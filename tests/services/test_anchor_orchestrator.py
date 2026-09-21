"""Tests for the SEL-X anchor orchestrator service."""

from __future__ import annotations

import pytest

testing_postgresql = pytest.importorskip("testing.postgresql")

from egregore.domain.execution_block import CausalVector, ExecutionBlock
from egregore.domain.execution_record import ExecutionRecord, PolicyContext
from egregore.infrastructure.postgres_anchor_store import PostgresAnchorStore
from egregore.infrastructure.postgres_block_store import PostgresBlockStore
from egregore.kernel.ed25519_signer import generate_signing_key
from egregore.services.anchor_orchestrator.service import AnchorOrchestrator
from egregore.services.anchor_orchestrator.timestamp_client import (
    MockTimestampClient,
    TimestampError,
)
from egregore.shared.freeze_state import FreezeController, FreezeState


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
    ).with_integrity_hash()


def _block(tenant_id: str = "t1", seq: int = 0) -> ExecutionBlock:
    return ExecutionBlock(
        block_id=f"b{seq}",
        block_seq=seq,
        created_at_ns=seq + 1,
        records=(_record(tenant_id=tenant_id),),
        merkle_root="root",
        previous_block_hash="0" * 64 if seq == 0 else "p" * 64,
        causal_vector=CausalVector(),
    ).with_integrity_hash()


class _FailingTimestampClient:
    """Test double that always raises TimestampError."""

    def timestamp(self, data_hash: str):
        raise TimestampError("simulated TSA failure")


@pytest.fixture(scope="function")
def orchestrator():
    generate_signing_key()
    with testing_postgresql.Postgresql() as postgresql:
        block_store = PostgresBlockStore(postgresql.url())
        anchor_store = PostgresAnchorStore(postgresql.url())
        orch = AnchorOrchestrator(
            block_store=block_store,
            anchor_store=anchor_store,
            timestamp_client=MockTimestampClient(),
        )
        yield orch


@pytest.fixture(scope="function")
def failing_orchestrator():
    with testing_postgresql.Postgresql() as postgresql:
        block_store = PostgresBlockStore(postgresql.url())
        anchor_store = PostgresAnchorStore(postgresql.url())
        freeze_controller = FreezeController()
        orch = AnchorOrchestrator(
            block_store=block_store,
            anchor_store=anchor_store,
            timestamp_client=_FailingTimestampClient(),
            freeze_controller=freeze_controller,
        )
        yield orch, freeze_controller


def test_anchor_block_creates_record(orchestrator: AnchorOrchestrator) -> None:
    block = _block()
    orchestrator._block_store.append(block)  # type: ignore[attr-defined]

    records = list(orchestrator.anchor_unanchored_blocks("t1"))
    assert len(records) == 1
    assert records[0].block_hash == block.integrity_hash
    assert records[0].tier == "0"


def test_already_anchored_block_is_skipped(orchestrator: AnchorOrchestrator) -> None:
    block = _block()
    orchestrator._block_store.append(block)  # type: ignore[attr-defined]

    list(orchestrator.anchor_unanchored_blocks("t1"))
    records = list(orchestrator.anchor_unanchored_blocks("t1"))
    assert len(records) == 0


def test_anchor_id_is_deterministic() -> None:
    h = "a" * 64
    id1 = AnchorOrchestrator._derive_anchor_id(h)
    id2 = AnchorOrchestrator._derive_anchor_id(h)
    assert id1 == id2
    assert len(id1) == 64


def test_successful_anchor_keeps_freeze_controller_healthy(
    orchestrator: AnchorOrchestrator,
) -> None:
    freeze_controller = FreezeController()
    orchestrator._freeze_controller = freeze_controller
    block = _block()
    orchestrator._block_store.append(block)

    list(orchestrator.anchor_unanchored_blocks("t1"))
    assert freeze_controller.state == FreezeState.HEALTHY


def test_anchor_failure_triggers_auto_freeze(failing_orchestrator) -> None:
    orch, freeze_controller = failing_orchestrator
    block = _block()
    orch._block_store.append(block)

    with pytest.raises(TimestampError):
        list(orch.anchor_unanchored_blocks("t1"))
    assert freeze_controller.state == FreezeState.FROZEN
    assert freeze_controller.is_frozen is True


def test_anchor_failure_without_freeze_controller_still_raises(
    failing_orchestrator,
) -> None:
    orch, _ = failing_orchestrator
    orch._freeze_controller = None
    block = _block()
    orch._block_store.append(block)

    with pytest.raises(TimestampError):
        list(orch.anchor_unanchored_blocks("t1"))
