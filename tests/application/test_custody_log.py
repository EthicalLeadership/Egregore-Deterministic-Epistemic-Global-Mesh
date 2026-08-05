"""Tests for chain-of-custody events over the .zarc chain."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from egregore.application.custody_log import CustodyLog
from egregore.domain.custody import (
    CustodyError,
    create_custody_event,
    validate_custody_continuity,
)
from egregore.kernel.ed25519_signer import generate_signing_key
from egregore.kernel.provenance import Provenance

EVIDENCE_HASH = hashlib.sha256(b"evidence-bytes").hexdigest()


def _log(tmp_path: Path) -> tuple[CustodyLog, Provenance]:
    provenance = Provenance(tmp_path / "custody.zarc", signing_key_hex=generate_signing_key())
    return CustodyLog(provenance), provenance


def _acquire(ts: int = 1000, evidence_id: str = "EV-001"):
    return create_custody_event(
        evidence_id=evidence_id,
        action="acquire",
        actor="agent-smith",
        role="investigator",
        timestamp_ns=ts,
        evidence_hash=EVIDENCE_HASH,
        location="lab-1",
        purpose="intake",
    )


class TestEventValidation:
    def test_transfer_requires_both_custodians(self):
        with pytest.raises(CustodyError, match="from_custodian and to_custodian"):
            create_custody_event(
                evidence_id="EV", action="transfer", actor="a", role="r",
                timestamp_ns=1, to_custodian="b",
            )

    def test_transfer_custodians_must_differ(self):
        with pytest.raises(CustodyError, match="must differ"):
            create_custody_event(
                evidence_id="EV", action="transfer", actor="a", role="r",
                timestamp_ns=1, from_custodian="x", to_custodian="x",
            )

    def test_seal_requires_evidence_hash(self):
        with pytest.raises(CustodyError, match="evidence_hash"):
            create_custody_event(
                evidence_id="EV", action="seal", actor="a", role="r", timestamp_ns=1,
            )

    def test_unknown_action_rejected(self):
        with pytest.raises(CustodyError, match="Unknown custody action"):
            create_custody_event(
                evidence_id="EV", action="destroy", actor="a", role="r", timestamp_ns=1,
            )

    def test_non_transfer_cannot_carry_custodian_fields(self):
        with pytest.raises(CustodyError, match="must not carry"):
            create_custody_event(
                evidence_id="EV", action="examine", actor="a", role="r",
                timestamp_ns=1, from_custodian="x",
            )

    def test_event_id_deterministic(self):
        first, second = _acquire(), _acquire()
        assert first.custody_event_id == second.custody_event_id
        assert first.custody_event_id != _acquire(ts=2000).custody_event_id


class TestContinuity:
    def test_must_begin_with_acquire(self):
        transfer = create_custody_event(
            evidence_id="EV", action="transfer", actor="a", role="r",
            timestamp_ns=1, from_custodian="a", to_custodian="b",
        )
        with pytest.raises(CustodyError, match="must begin with acquire"):
            validate_custody_continuity([transfer])

    def test_transfer_from_non_holder_breaks(self):
        transfer = create_custody_event(
            evidence_id="EV", action="transfer", actor="mallory", role="r",
            timestamp_ns=2, from_custodian="mallory", to_custodian="bob",
        )
        with pytest.raises(CustodyError, match="Custody break"):
            validate_custody_continuity([_acquire(), transfer])

    def test_duplicate_acquire_rejected(self):
        with pytest.raises(CustodyError, match="Duplicate acquire"):
            validate_custody_continuity([_acquire(), _acquire(ts=2000)])

    def test_empty_chain_rejected(self):
        with pytest.raises(CustodyError, match="Empty custody chain"):
            validate_custody_continuity([])


class TestCustodyLog:
    def test_full_lifecycle(self, tmp_path: Path):
        log, provenance = _log(tmp_path)
        log.record(_acquire())
        log.record(
            create_custody_event(
                evidence_id="EV-001", action="transfer", actor="agent-smith",
                role="investigator", timestamp_ns=2000,
                from_custodian="agent-smith", to_custodian="lab-jones",
            )
        )
        log.record(
            create_custody_event(
                evidence_id="EV-001", action="seal", actor="lab-jones",
                role="analyst", timestamp_ns=3000, evidence_hash=EVIDENCE_HASH,
            )
        )
        history = log.history("EV-001")
        assert history.custodian == "lab-jones"
        assert history.sealed
        assert [e.action for e in history.events] == ["acquire", "transfer", "seal"]
        # Custody entries verify as ordinary signed chain lines.
        assert provenance.verify_chain()

    def test_unlawful_transfer_refused_at_record_time(self, tmp_path: Path):
        log, _ = _log(tmp_path)
        log.record(_acquire())
        with pytest.raises(CustodyError, match="Custody break"):
            log.record(
                create_custody_event(
                    evidence_id="EV-001", action="transfer", actor="mallory",
                    role="r", timestamp_ns=2000,
                    from_custodian="mallory", to_custodian="bob",
                )
            )
        # Chain remains clean after the refusal.
        history = log.history("EV-001")
        assert history.custodian == "agent-smith"

    def test_record_for_unknown_evidence_refused(self, tmp_path: Path):
        log, _ = _log(tmp_path)
        with pytest.raises(CustodyError):
            log.record(
                create_custody_event(
                    evidence_id="EV-GHOST", action="seal", actor="a", role="r",
                    timestamp_ns=1, evidence_hash=EVIDENCE_HASH,
                )
            )

    def test_history_reconstructs_after_reopen(self, tmp_path: Path):
        log, provenance = _log(tmp_path)
        log.record(_acquire())
        # Reopen the same chain file with a new writer (append semantics).
        from egregore.kernel.ed25519_signer import generate_signing_key
        from egregore.kernel.provenance import Provenance as _P

        _ = generate_signing_key  # noqa: F841
        reopened = _P(tmp_path / "custody.zarc", signing_key_hex=generate_signing_key())
        history = CustodyLog(reopened).history("EV-001")
        assert history.custodian == "agent-smith"

    def test_multi_evidence_isolation(self, tmp_path: Path):
        log, _ = _log(tmp_path)
        log.record(_acquire(evidence_id="EV-A"))
        log.record(_acquire(evidence_id="EV-B", ts=1500))
        assert log.history("EV-A").custodian == "agent-smith"
        assert len(log.history("EV-B").events) == 1
