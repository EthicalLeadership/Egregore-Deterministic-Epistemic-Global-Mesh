"""Tests for ANCHORUM provenance validation and optional Ed25519 signing."""

from __future__ import annotations

import os

import pytest
from nacl.signing import SigningKey  # type: ignore[import-untyped]

from anchorum.forensic.core.provenance import (
    ZarcEventType,
    clear_events,
    emit_zarc_event,
    emitted_events,
)
from anchorum.forensic.core.validation import (
    validate_case_id,
    validate_operator,
)


def test_validate_case_id_accepts_safe_identifier() -> None:
    validate_case_id("CASE-2026_001")


@pytest.mark.parametrize("case_id", ["", "CASE 001", "CASE/001", "CASE*001"])
def test_validate_case_id_rejects_unsafe(case_id: str) -> None:
    with pytest.raises(ValueError):
        validate_case_id(case_id)


def test_validate_operator_accepts_safe_identifier() -> None:
    validate_operator("alice.doe")


@pytest.mark.parametrize("operator", ["", "alice doe", "alice/doe"])
def test_validate_operator_rejects_unsafe(operator: str) -> None:
    with pytest.raises(ValueError):
        validate_operator(operator)


def test_emit_zarc_event_rejects_bad_case_id() -> None:
    with pytest.raises(ValueError):
        emit_zarc_event(
            event_type=ZarcEventType.PDF_LIBERATION,
            case_id="bad case",
            operator="alice",
            payload={},
        )


def test_emit_zarc_event_rejects_bad_operator() -> None:
    with pytest.raises(ValueError):
        emit_zarc_event(
            event_type=ZarcEventType.PDF_LIBERATION,
            case_id="CASE-001",
            operator="",
            payload={},
        )


def test_emit_zarc_event_writes_signature_when_key_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_events()
    signing_key = SigningKey(os.urandom(32))
    monkeypatch.setenv("ANCHORUM_SIGNING_KEY", signing_key.encode().hex())

    event_id = emit_zarc_event(
        event_type=ZarcEventType.PDF_LIBERATION,
        case_id="CASE-001",
        operator="alice",
        payload={"hash": "abc"},
    )
    assert event_id
    event = emitted_events()[0]
    assert "signature_hex" in event
    assert "signer_public_key_hex" in event
    assert event["signer_public_key_hex"] == signing_key.verify_key.encode().hex()
