"""Custody log — chain-of-custody over the signed `.zarc` chain.

Custody events are appended as ``engine="custody"`` `.zarc` entries, so the
custody record inherits the chain's Ed25519 signatures and hash linkage.
History reconstruction validates continuity fail-closed: a broken custody
chain is an error, never a silently tolerated gap.
"""

from __future__ import annotations

from dataclasses import dataclass

from egregore.domain.custody import (
    CustodyError,
    CustodyEvent,
    current_custodian,
    validate_custody_continuity,
)
from egregore.kernel.provenance import Provenance

ENGINE = "custody"


@dataclass(frozen=True)
class CustodyHistory:
    evidence_id: str
    events: tuple[CustodyEvent, ...]
    custodian: str

    @property
    def sealed(self) -> bool:
        return any(event.action == "seal" for event in self.events)


class CustodyLog:
    """Append and reconstruct custody records on a `.zarc` chain."""

    def __init__(self, provenance: Provenance) -> None:
        self._provenance = provenance

    def record(self, event: CustodyEvent) -> str:
        """Append a custody event; returns the new chain head hash.

        Continuity is validated against the recorded history *before*
        appending — an unlawful transfer is refused, not just flagged.
        """
        existing = self._events_for(event.evidence_id)
        if existing or event.action == "acquire":
            validate_custody_continuity((*existing, event))
        else:
            # Non-acquire event for unknown evidence: refuse.
            validate_custody_continuity([])
        return self._provenance.append(
            engine=ENGINE,
            event=f"custody.{event.action}",
            payload=event.to_payload(),
            ts_ns=event.timestamp_ns,
        )

    def history(self, evidence_id: str) -> CustodyHistory:
        """Reconstruct and validate the full custody lifecycle."""
        events = self._events_for(evidence_id)
        validate_custody_continuity(events)
        return CustodyHistory(
            evidence_id=evidence_id,
            events=tuple(events),
            custodian=current_custodian(events),
        )

    def _events_for(self, evidence_id: str) -> list[CustodyEvent]:
        events: list[CustodyEvent] = []
        for entry in self._provenance.iter_entries():
            if entry.engine != ENGINE:
                continue
            payload = entry.payload
            if payload.get("evidence_id") != evidence_id:
                continue
            events.append(CustodyEvent.from_payload(payload))
        return events
