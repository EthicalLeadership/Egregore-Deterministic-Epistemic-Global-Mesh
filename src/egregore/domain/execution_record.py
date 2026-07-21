"""Canonical execution record schema for SEL-X.

Unifies identity, causality, policy, budget, and integrity metadata into a
single, hash-chained record that the block builder can aggregate.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from egregore.domain.semantics_models import StableErrorCode


@dataclass(frozen=True)
class PolicyContext:
    """Deterministic policy metadata bound to an execution record."""

    policy_version: str
    engine_version: str
    evaluation_hash: str | None = None
    input_context_hash: str | None = None
    decision_hash: str | None = None


@dataclass(frozen=True)
class BudgetContext:
    """Budget atomicity metadata for an execution record."""

    budget_id: str
    pre_balance: int
    post_balance: int
    cost_units: int = 0
    currency: str = "credits"


@dataclass(frozen=True)
class ExecutionRecord:
    """Single canonical record describing one guarded execution.

    Fields are chosen to satisfy SEL-X requirements:
    - deterministic identity
    - principal/role/tenant authorization context
    - policy and budget context
    - input/output integrity hashes
    - hash-chain linkage to previous record
    """

    record_id: str
    timestamp_ns: int
    tenant_id: str
    principal_id: str
    role: str
    session_id: str
    trace_id: str
    subsystem: str
    operation: str

    policy_context: PolicyContext
    budget_context: BudgetContext | None = None

    input_hash: str | None = None
    output_hash: str | None = None
    previous_record_hash: str | None = None
    integrity_hash: str | None = None

    payload: Mapping[str, Any] = field(default_factory=dict)
    success: bool = True
    error_code: StableErrorCode | None = None
    error_message: str | None = None

    def compute_integrity_hash(self) -> str:
        """Deterministic SHA-256 over the record's canonical fields."""
        # Build a deterministic string representation.
        parts = [
            self.record_id,
            str(self.timestamp_ns),
            self.tenant_id,
            self.principal_id,
            self.role,
            self.session_id,
            self.trace_id,
            self.subsystem,
            self.operation,
            self.policy_context.policy_version,
            self.policy_context.engine_version,
            self.policy_context.evaluation_hash or "",
            self.policy_context.input_context_hash or "",
            self.policy_context.decision_hash or "",
            str(self.budget_context.budget_id) if self.budget_context else "",
            str(self.budget_context.pre_balance) if self.budget_context else "",
            str(self.budget_context.post_balance) if self.budget_context else "",
            self.input_hash or "",
            self.output_hash or "",
            self.previous_record_hash or "",
            str(self.success),
            str(self.error_code.value) if self.error_code else "",
            self.error_message or "",
        ]
        joined = "|".join(parts)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def with_integrity_hash(self) -> ExecutionRecord:
        """Return a new record with the integrity hash computed and set."""
        integrity = self.compute_integrity_hash()
        return ExecutionRecord(
            record_id=self.record_id,
            timestamp_ns=self.timestamp_ns,
            tenant_id=self.tenant_id,
            principal_id=self.principal_id,
            role=self.role,
            session_id=self.session_id,
            trace_id=self.trace_id,
            subsystem=self.subsystem,
            operation=self.operation,
            policy_context=self.policy_context,
            budget_context=self.budget_context,
            input_hash=self.input_hash,
            output_hash=self.output_hash,
            previous_record_hash=self.previous_record_hash,
            integrity_hash=integrity,
            payload=self.payload,
            success=self.success,
            error_code=self.error_code,
            error_message=self.error_message,
        )


def generate_record_id(
    *,
    trace_id: str,
    timestamp_ns: int,
    operation: str,
    sequence: int = 0,
) -> str:
    """Deterministic record ID from trace + timestamp + operation + sequence."""
    material = f"{trace_id}|{timestamp_ns}|{operation}|{sequence}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def generate_previous_record_hash(previous: ExecutionRecord | None) -> str:
    """Hash of the previous record's integrity hash; genesis if none."""
    if previous is None or previous.integrity_hash is None:
        return "0" * 64
    return hashlib.sha256(previous.integrity_hash.encode("utf-8")).hexdigest()
