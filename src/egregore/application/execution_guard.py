"""ExecutionGuard - single mandatory entrypoint for all execution in Egregore."""

import hashlib
import logging
import time
from collections.abc import Callable
from typing import Any

from egregore.domain.execution_context import ExecutionContext
from egregore.domain.execution_record import (
    BudgetContext,
    ExecutionRecord,
    PolicyContext,
    generate_previous_record_hash,
    generate_record_id,
)
from egregore.domain.semantics_models import StableErrorCode
from egregore.domain.structured_failure import StructuredFailure
from egregore.shared.canonical import canonical_dumps

logger = logging.getLogger("egregore.execution_guard")


class ExecutionGuard:
    """
    Wraps every handler invocation with:
      - SEL-X guard policy validation (identity, role, policy, budget, feature flags)
      - identity context preservation
      - input/output hashing
      - duration measurement
      - structured logging
      - canonical ExecutionRecord emission
      - exception-to-StructuredFailure conversion

    No execution may bypass this guard.
    """

    _last_record_hash: str = "0" * 64

    @classmethod
    def execute(
        cls,
        context: ExecutionContext,
        handler: Callable[..., Any],
        *args: Any,
        guard_policy: Any | None = None,
        estimated_cost: int = 0,
        required_feature_flag: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Execute *handler* under *context*, logging the full lifecycle.

        Args:
            context: immutable execution identity/causality context
            handler: callable to execute
            *args: positional arguments passed to handler
            guard_policy: optional SEL-X policy validator
            estimated_cost: budget units to reserve
            required_feature_flag: flag that must be enabled
            **kwargs: keyword arguments passed to handler

        Raises:
            Exception: The original exception, after logging a StructuredFailure.

        """
        start_time = time.perf_counter()
        timestamp_ns = time.time_ns()

        policy_context = PolicyContext(
            policy_version="unknown", engine_version="unknown"
        )
        budget_context: BudgetContext | None = None

        try:
            if guard_policy is not None:
                policy_context, budget_context = guard_policy.validate_all(
                    context,
                    estimated_cost=estimated_cost,
                    required_feature_flag=required_feature_flag,
                )

            input_hash = ExecutionGuard._hash_payload(args, kwargs)
            result = handler(*args, **kwargs)
            output_hash = ExecutionGuard._hash_payload(result)
            duration_ms = int((time.perf_counter() - start_time) * 1000)

            record = cls._build_record(
                context=context,
                timestamp_ns=timestamp_ns,
                policy_context=policy_context,
                budget_context=budget_context,
                input_hash=input_hash,
                output_hash=output_hash,
                duration_ms=duration_ms,
                success=True,
                failure=None,
            )
            cls._emit_record(record)
            cls._last_record_hash = record.integrity_hash or cls._last_record_hash

            ExecutionGuard._log_execution(
                context=context,
                input_hash=input_hash,
                output_hash=output_hash,
                duration_ms=duration_ms,
                success=True,
                failure=None,
            )

            return result

        except Exception as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            failure = StructuredFailure.from_exception(
                e, subsystem=context.subsystem, operation=context.operation
            )

            error_code = StableErrorCode.ENGINE_FAILED
            if hasattr(e, "code") and isinstance(e.code, StableErrorCode):
                error_code = e.code

            record = cls._build_record(
                context=context,
                timestamp_ns=timestamp_ns,
                policy_context=policy_context,
                budget_context=budget_context,
                input_hash=None,
                output_hash=None,
                duration_ms=duration_ms,
                success=False,
                failure=failure,
                error_code=error_code,
                error_message=str(e),
            )
            cls._emit_record(record)
            cls._last_record_hash = record.integrity_hash or cls._last_record_hash

            ExecutionGuard._log_execution(
                context=context,
                input_hash=None,
                output_hash=None,
                duration_ms=duration_ms,
                success=False,
                failure=failure,
            )

            raise

    @classmethod
    def _build_record(
        cls,
        *,
        context: ExecutionContext,
        timestamp_ns: int,
        policy_context: PolicyContext,
        budget_context: BudgetContext | None,
        input_hash: str | None,
        output_hash: str | None,
        duration_ms: int,
        success: bool,
        failure: StructuredFailure | None,
        error_code: StableErrorCode | None = None,
        error_message: str | None = None,
    ) -> ExecutionRecord:
        record_id = generate_record_id(
            trace_id=context.trace_id,
            timestamp_ns=timestamp_ns,
            operation=context.operation,
            sequence=0,
        )
        previous_hash = generate_previous_record_hash(None)
        # Link to the previous emitted record by its integrity hash.
        if cls._last_record_hash != "0" * 64:
            previous_hash = hashlib.sha256(
                cls._last_record_hash.encode("utf-8")
            ).hexdigest()

        record = ExecutionRecord(
            record_id=record_id,
            timestamp_ns=timestamp_ns,
            tenant_id=context.tenant_id,
            principal_id=context.principal_id,
            role=context.role,
            session_id=context.session_id,
            trace_id=context.trace_id,
            subsystem=context.subsystem,
            operation=context.operation,
            policy_context=policy_context,
            budget_context=budget_context,
            input_hash=input_hash,
            output_hash=output_hash,
            previous_record_hash=previous_hash,
            payload={"duration_ms": duration_ms},
            success=success,
            error_code=error_code,
            error_message=error_message,
        )
        return record.with_integrity_hash()

    @staticmethod
    def _emit_record(record: ExecutionRecord) -> None:
        """Emit the canonical execution record to the structured log."""
        logger.info(
            "EXECUTION_RECORD %s",
            canonical_dumps(
                {
                    "record_id": record.record_id,
                    "integrity_hash": record.integrity_hash,
                    "previous_record_hash": record.previous_record_hash,
                    "tenant_id": record.tenant_id,
                    "principal_id": record.principal_id,
                    "operation": record.operation,
                    "success": record.success,
                },
                default=str,
            ),
        )

    @staticmethod
    def _hash_payload(*data: Any) -> str:
        """Deterministic SHA-256 over canonical JSON representation."""
        try:
            serialized = canonical_dumps(data)
        except TypeError:
            serialized = canonical_dumps(data, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _log_execution(
        context: ExecutionContext,
        input_hash: str | None,
        output_hash: str | None,
        duration_ms: int,
        success: bool,
        failure: StructuredFailure | None,
    ) -> None:
        """Emit a single structured execution log line."""
        log_entry = {
            "event_type": "execution",
            "trace_id": context.trace_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "role": context.role,
            "session_id": context.session_id,
            "subsystem": context.subsystem,
            "operation": context.operation,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "duration_ms": duration_ms,
            "success": success,
            "failure": (
                {
                    "failure_id": failure.failure_id,
                    "message": failure.message,
                    "severity": failure.severity,
                }
                if failure
                else None
            ),
        }
        try:
            serialized = canonical_dumps(log_entry, default=str)
        except TypeError:
            serialized = str(log_entry)
        logger.info("EXECUTION_LOG %s", serialized)
