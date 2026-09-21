"""SEL-X freeze protocol state machine.

Domain model for integrity-failure handling. When a fork or tamper event is
detected, the system must be able to freeze writes, enter reconciliation, and
resume only after explicit clearance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class FreezeState(Enum):
    HEALTHY = auto()
    FROZEN = auto()
    RECONCILING = auto()


@dataclass(frozen=True)
class FreezeEvent:
    state: FreezeState
    reason: str
    timestamp_ns: int
    detection_source: str
    operator_id: str | None = None
    block_hash_trigger: str | None = None
    stored_hash: str | None = None
    recomputed_hash: str | None = None
    signature_valid: bool | None = None
    context: dict[str, Any] = field(default_factory=dict)


class FreezeController:
    """State machine: HEALTHY -> FROZEN -> RECONCILING -> HEALTHY."""

    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id
        self._state = FreezeState.HEALTHY
        self._history: list[FreezeEvent] = []
        self._frozen_reason: str | None = None

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def state(self) -> FreezeState:
        return self._state

    @property
    def is_frozen(self) -> bool:
        return self._state == FreezeState.FROZEN

    @property
    def is_reconciling(self) -> bool:
        return self._state == FreezeState.RECONCILING

    @property
    def history(self) -> tuple[FreezeEvent, ...]:
        return tuple(self._history)

    def fork_detected(
        self,
        reason: str,
        timestamp_ns: int | None = None,
        detection_source: str = "integrity_gate",
        **context: Any,
    ) -> None:
        """Record a fork detection and freeze writes.

        Backward-compatible behavior: appends a fork event, then calls
        freeze_writes. From a healthy state this produces two history entries.
        """
        ts = timestamp_ns if timestamp_ns is not None else self._now_ns()
        self._history.append(
            FreezeEvent(
                state=FreezeState.FROZEN,
                reason=reason,
                timestamp_ns=ts,
                detection_source=detection_source,
                **context,
            )
        )
        self.freeze_writes(reason, ts, detection_source, **context)

    def freeze_writes(
        self,
        reason: str,
        timestamp_ns: int | None = None,
        detection_source: str = "integrity_gate",
        **context: Any,
    ) -> None:
        """Explicit legacy freeze call. Idempotent: only acts from HEALTHY."""
        ts = timestamp_ns if timestamp_ns is not None else self._now_ns()
        if self._state != FreezeState.HEALTHY:
            return
        self._state = FreezeState.FROZEN
        self._frozen_reason = reason
        self._history.append(
            FreezeEvent(
                state=FreezeState.FROZEN,
                reason=reason,
                timestamp_ns=ts,
                detection_source=detection_source,
                **context,
            )
        )

    def freeze(
        self,
        reason: str,
        timestamp_ns: int | None = None,
        detection_source: str = "integrity_gate",
        **context: Any,
    ) -> FreezeEvent:
        """Freeze the tenant and record a forensic event.

        Unlike freeze_writes, this always records an event so that every
        tamper signal is auditable, even if already frozen.
        """
        ts = timestamp_ns if timestamp_ns is not None else self._now_ns()
        event = FreezeEvent(
            state=FreezeState.FROZEN,
            reason=reason,
            timestamp_ns=ts,
            detection_source=detection_source,
            block_hash_trigger=context.get("block_hash_trigger"),
            stored_hash=context.get("stored_hash"),
            recomputed_hash=context.get("recomputed_hash"),
            signature_valid=context.get("signature_valid"),
            context=context,
        )
        self._history.append(event)
        if self._state == FreezeState.HEALTHY:
            self._state = FreezeState.FROZEN
            self._frozen_reason = reason
        return event

    def integrity_breach(
        self,
        reason: str,
        timestamp_ns: int | None = None,
        detection_source: str = "anchorum_integrity_gate",
        **context: Any,
    ) -> FreezeEvent:
        """Record an integrity-gate breach and freeze the tenant.

        This is semantically distinct from fork_detected(): it signals a
        failed pre-flight integrity check rather than an unauthorised code
        change. It always records an event and transitions from HEALTHY to
        FROZEN.
        """
        ts = timestamp_ns if timestamp_ns is not None else self._now_ns()
        event = FreezeEvent(
            state=FreezeState.FROZEN,
            reason=reason,
            timestamp_ns=ts,
            detection_source=detection_source,
            context=context,
        )
        self._history.append(event)
        if self._state == FreezeState.HEALTHY:
            self._state = FreezeState.FROZEN
            self._frozen_reason = reason
        return event

    def unfreeze(
        self,
        reason: str,
        operator_id: str,
        timestamp_ns: int | None = None,
    ) -> FreezeEvent:
        """Move from FROZEN to RECONCILING after operator review."""
        ts = timestamp_ns if timestamp_ns is not None else self._now_ns()
        if self._state != FreezeState.FROZEN:
            raise RuntimeError(f"Cannot unfreeze from {self._state.name}")
        self._state = FreezeState.RECONCILING
        event = FreezeEvent(
            state=FreezeState.RECONCILING,
            reason=f"Unfrozen: {reason}",
            timestamp_ns=ts,
            detection_source="operator",
            operator_id=operator_id,
        )
        self._history.append(event)
        return event

    def reset(
        self,
        reason: str,
        operator_id: str,
        timestamp_ns: int | None = None,
    ) -> FreezeEvent:
        """Move from RECONCILING to HEALTHY after sign-off."""
        ts = timestamp_ns if timestamp_ns is not None else self._now_ns()
        if self._state != FreezeState.RECONCILING:
            raise RuntimeError(f"Cannot reset from {self._state.name}")
        self._state = FreezeState.HEALTHY
        frozen_reason = self._frozen_reason or "unknown"
        self._frozen_reason = None
        event = FreezeEvent(
            state=FreezeState.HEALTHY,
            reason=f"Reset to HEALTHY (was: {frozen_reason}): {reason}",
            timestamp_ns=ts,
            detection_source="operator",
            operator_id=operator_id,
        )
        self._history.append(event)
        return event

    def reconciliation_required(
        self,
        timestamp_ns: int | None = None,
        detection_source: str = "operator",
    ) -> None:
        """Move from FROZEN to RECONCILING."""
        ts = timestamp_ns if timestamp_ns is not None else self._now_ns()
        if self._state != FreezeState.FROZEN:
            return
        self._state = FreezeState.RECONCILING
        self._history.append(
            FreezeEvent(
                FreezeState.RECONCILING,
                f"Reconciliation started: {self._frozen_reason}",
                ts,
                detection_source,
            )
        )

    def clear(
        self,
        timestamp_ns: int | None = None,
        detection_source: str = "operator",
    ) -> None:
        """Return to HEALTHY after reconciliation."""
        ts = timestamp_ns if timestamp_ns is not None else self._now_ns()
        if self._state == FreezeState.HEALTHY:
            return
        self._state = FreezeState.HEALTHY
        reason = self._frozen_reason or "unknown"
        self._frozen_reason = None
        self._history.append(
            FreezeEvent(
                FreezeState.HEALTHY,
                f"Cleared: {reason}",
                ts,
                detection_source,
            )
        )

    def require_unfrozen(self) -> None:
        """Call before write operations; raises if frozen."""
        if self._state == FreezeState.FROZEN:
            raise RuntimeError(f"Writes frozen: {self._frozen_reason}")

    @staticmethod
    def _now_ns() -> int:
        import time

        return time.time_ns()
