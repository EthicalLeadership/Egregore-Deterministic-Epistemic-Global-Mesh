"""Tests for the SEL-X freeze state machine."""

from __future__ import annotations

import pytest

from egregore.shared.freeze_state import FreezeController, FreezeState


def test_initial_state_is_healthy() -> None:
    fc = FreezeController()
    assert fc.state == FreezeState.HEALTHY
    assert fc.is_frozen is False


def test_fork_detected_freezes_writes() -> None:
    fc = FreezeController()
    fc.fork_detected("hash mismatch", timestamp_ns=1)
    assert fc.state == FreezeState.FROZEN
    assert fc.is_frozen is True
    assert len(fc.history) == 2  # FROZEN event recorded twice (fork + freeze)


def test_reconciliation_then_clear() -> None:
    fc = FreezeController()
    fc.fork_detected("hash mismatch", timestamp_ns=1)
    fc.reconciliation_required(timestamp_ns=2)
    assert fc.state == FreezeState.RECONCILING
    fc.clear(timestamp_ns=3)
    assert fc.state == FreezeState.HEALTHY


def test_require_unfrozen_raises_when_frozen() -> None:
    fc = FreezeController()
    fc.freeze_writes("integrity failure", timestamp_ns=1)
    with pytest.raises(RuntimeError, match="Writes frozen"):
        fc.require_unfrozen()


def test_require_unfrozen_passes_when_healthy() -> None:
    fc = FreezeController()
    fc.require_unfrozen()  # no exception


def test_freeze_idempotent() -> None:
    fc = FreezeController()
    fc.freeze_writes("reason", timestamp_ns=1)
    fc.freeze_writes("other", timestamp_ns=2)
    assert fc.state == FreezeState.FROZEN
    assert fc._frozen_reason == "reason"


def test_integrity_breach_freezes_and_records_event() -> None:
    fc = FreezeController()
    event = fc.integrity_breach("anchorum failure", timestamp_ns=42)
    assert fc.state == FreezeState.FROZEN
    assert fc.is_frozen is True
    assert event.detection_source == "anchorum_integrity_gate"
    assert event.reason == "anchorum failure"
    assert event.timestamp_ns == 42
    assert len(fc.history) == 1
