"""Tests for the canonical SEL-X ExecutionRecord schema."""

from __future__ import annotations

from egregore.domain.execution_record import (
    BudgetContext,
    ExecutionRecord,
    PolicyContext,
    generate_previous_record_hash,
    generate_record_id,
)
from egregore.domain.semantics_models import StableErrorCode


def _policy() -> PolicyContext:
    return PolicyContext(
        policy_version="v1.0",
        engine_version="v1.0",
        evaluation_hash="eval_hash",
        input_context_hash="input_hash",
        decision_hash="decision_hash",
    )


def _budget() -> BudgetContext:
    return BudgetContext(
        budget_id="budget-1",
        pre_balance=100,
        post_balance=95,
        cost_units=5,
        currency="credits",
    )


def test_record_id_is_deterministic() -> None:
    rid1 = generate_record_id(trace_id="t1", timestamp_ns=1_000_000, operation="op")
    rid2 = generate_record_id(trace_id="t1", timestamp_ns=1_000_000, operation="op")
    rid3 = generate_record_id(trace_id="t2", timestamp_ns=1_000_000, operation="op")
    assert rid1 == rid2
    assert rid1 != rid3


def test_record_integrity_hash_covers_all_fields() -> None:
    record = ExecutionRecord(
        record_id="r1",
        timestamp_ns=1_000_000,
        tenant_id="tenant-1",
        principal_id="user-1",
        role="admin",
        session_id="session-1",
        trace_id="trace-1",
        subsystem="test",
        operation="op",
        policy_context=_policy(),
        budget_context=_budget(),
        input_hash="input",
        output_hash="output",
        previous_record_hash="0" * 64,
        success=True,
    ).with_integrity_hash()

    assert record.integrity_hash is not None
    assert len(record.integrity_hash) == 64


def test_record_integrity_hash_changes_when_field_changes() -> None:
    base = ExecutionRecord(
        record_id="r1",
        timestamp_ns=1_000_000,
        tenant_id="tenant-1",
        principal_id="user-1",
        role="admin",
        session_id="session-1",
        trace_id="trace-1",
        subsystem="test",
        operation="op",
        policy_context=_policy(),
        budget_context=_budget(),
        input_hash="input",
        output_hash="output",
        previous_record_hash="0" * 64,
        success=True,
    )
    h1 = base.with_integrity_hash().integrity_hash

    changed = ExecutionRecord(
        record_id="r1",
        timestamp_ns=1_000_000,
        tenant_id="tenant-1",
        principal_id="user-1",
        role="admin",
        session_id="session-1",
        trace_id="trace-1",
        subsystem="test",
        operation="op",
        policy_context=PolicyContext(policy_version="v2.0", engine_version="v1.0"),
        budget_context=_budget(),
        input_hash="input",
        output_hash="output",
        previous_record_hash="0" * 64,
        success=True,
    )
    h2 = changed.with_integrity_hash().integrity_hash
    assert h1 != h2


def test_previous_record_hash_genesis() -> None:
    assert generate_previous_record_hash(None) == "0" * 64


def test_previous_record_hash_links_to_prior() -> None:
    previous = ExecutionRecord(
        record_id="r0",
        timestamp_ns=1,
        tenant_id="t",
        principal_id="u",
        role="r",
        session_id="s",
        trace_id="tr",
        subsystem="sub",
        operation="op",
        policy_context=PolicyContext(policy_version="v1", engine_version="v1"),
        integrity_hash="abc123",
    )
    h = generate_previous_record_hash(previous)
    assert h != "0" * 64
    assert len(h) == 64


def test_error_record_fields() -> None:
    record = ExecutionRecord(
        record_id="r1",
        timestamp_ns=1,
        tenant_id="t",
        principal_id="u",
        role="r",
        session_id="s",
        trace_id="tr",
        subsystem="sub",
        operation="op",
        policy_context=PolicyContext(policy_version="v1", engine_version="v1"),
        success=False,
        error_code=StableErrorCode.ENGINE_FAILED,
        error_message="boom",
    ).with_integrity_hash()

    assert record.success is False
    assert record.error_code == StableErrorCode.ENGINE_FAILED
    assert record.error_message == "boom"
    assert record.integrity_hash is not None
