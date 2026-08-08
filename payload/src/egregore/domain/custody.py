# epistemic marker: provenance / chain-of-custody
"""Chain-of-custody domain model (court-grade evidence handling).

Custody events ride the existing `.zarc` chain as ``engine="custody"``
entries — the overlay introduces no format change, so every historical
hash stays valid while custody records inherit the signed hash-chain.

Semantics (fail-closed everywhere):
- every piece of evidence begins life with ``acquire``;
- ``transfer`` moves custody and must name both custodians; a transfer
  from a party that does not currently hold custody breaks the chain;
- ``seal`` / ``export`` / ``verify`` must bind to the evidence content
  hash (SHA-256 of the bytes they describe);
- timestamps are injected; this module does no I/O and no wall-clock.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from egregore.shared.canonical import sha256_hex

CUSTODY_ACTIONS = frozenset(
    {"acquire", "transfer", "examine", "seal", "export", "verify"}
)

# Actions that must bind to the evidence content hash.
HASH_REQUIRED_ACTIONS = frozenset({"acquire", "seal", "export", "verify"})

_HASH64 = re.compile(r"^[0-9a-f]{64}$")


class CustodyError(Exception):
    """Fail-closed custody violation."""


@dataclass(frozen=True)
class CustodyEvent:
    custody_event_id: str
    evidence_id: str
    action: str
    actor: str
    role: str
    timestamp_ns: int
    from_custodian: str | None = None
    to_custodian: str | None = None
    location: str = ""
    purpose: str = ""
    evidence_hash: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "custody_event_id": self.custody_event_id,
            "evidence_id": self.evidence_id,
            "action": self.action,
            "actor": self.actor,
            "role": self.role,
            "timestamp_ns": self.timestamp_ns,
            "from_custodian": self.from_custodian,
            "to_custodian": self.to_custodian,
            "location": self.location,
            "purpose": self.purpose,
            "evidence_hash": self.evidence_hash,
        }

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> CustodyEvent:
        return CustodyEvent(
            custody_event_id=str(payload["custody_event_id"]),
            evidence_id=str(payload["evidence_id"]),
            action=str(payload["action"]),
            actor=str(payload["actor"]),
            role=str(payload["role"]),
            timestamp_ns=int(payload["timestamp_ns"]),
            from_custodian=payload.get("from_custodian"),
            to_custodian=payload.get("to_custodian"),
            location=str(payload.get("location", "")),
            purpose=str(payload.get("purpose", "")),
            evidence_hash=payload.get("evidence_hash"),
        )


def derive_custody_event_id(
    evidence_id: str, action: str, actor: str, timestamp_ns: int
) -> str:
    """Deterministic custody event identity."""
    return sha256_hex(
        f"custody|{evidence_id}|{action}|{actor}|{timestamp_ns}".encode()
    )


def create_custody_event(
    *,
    evidence_id: str,
    action: str,
    actor: str,
    role: str,
    timestamp_ns: int,
    from_custodian: str | None = None,
    to_custodian: str | None = None,
    location: str = "",
    purpose: str = "",
    evidence_hash: str | None = None,
) -> CustodyEvent:
    """Build a validated custody event. Fail-closed on any violation."""
    if not evidence_id:
        raise CustodyError("evidence_id cannot be empty")
    if action not in CUSTODY_ACTIONS:
        raise CustodyError(f"Unknown custody action: {action!r}")
    if not actor:
        raise CustodyError("actor cannot be empty")
    if not role:
        raise CustodyError("role cannot be empty")

    if action == "transfer":
        if not from_custodian or not to_custodian:
            raise CustodyError("transfer requires from_custodian and to_custodian")
        if from_custodian == to_custodian:
            raise CustodyError("transfer custodians must differ")
    elif from_custodian is not None or to_custodian is not None:
        raise CustodyError(
            f"{action} must not carry custodian transfer fields"
        )

    if action in HASH_REQUIRED_ACTIONS:
        if evidence_hash is None or not _HASH64.match(evidence_hash):
            raise CustodyError(
                f"{action} requires a 64-hex SHA-256 evidence_hash"
            )
    elif evidence_hash is not None and not _HASH64.match(evidence_hash):
        raise CustodyError("evidence_hash must be 64-hex SHA-256")

    return CustodyEvent(
        custody_event_id=derive_custody_event_id(
            evidence_id, action, actor, timestamp_ns
        ),
        evidence_id=evidence_id,
        action=action,
        actor=actor,
        role=role,
        timestamp_ns=timestamp_ns,
        from_custodian=from_custodian,
        to_custodian=to_custodian,
        location=location,
        purpose=purpose,
        evidence_hash=evidence_hash,
    )


def current_custodian(events: Sequence[CustodyEvent]) -> str:
    """Current holder after a continuity-validated event sequence."""
    holder: str | None = None
    for event in events:
        if event.action == "acquire":
            holder = event.actor
        elif event.action == "transfer":
            holder = event.to_custodian
    if holder is None:
        raise CustodyError("No acquire event; custody never established")
    return holder


def validate_custody_continuity(events: Sequence[CustodyEvent]) -> None:
    """Validate an ordered custody chain. Fail-closed on any break.

    Rules:
    - the first event must be ``acquire``;
    - a ``transfer`` is lawful only from the current custodian;
    - exactly one ``acquire`` per evidence lifecycle.
    """
    if not events:
        raise CustodyError("Empty custody chain")
    if events[0].action != "acquire":
        raise CustodyError(
            f"Custody chain must begin with acquire, found {events[0].action!r}"
        )

    holder: str | None = None
    for index, event in enumerate(events):
        if event.action == "acquire":
            if index != 0:
                raise CustodyError(
                    f"Duplicate acquire at position {index}; evidence already held"
                )
            holder = event.actor
        elif event.action == "transfer":
            if holder is None:
                raise CustodyError("transfer before acquire")
            if event.from_custodian != holder:
                raise CustodyError(
                    f"Custody break at position {index}: transfer from "
                    f"{event.from_custodian!r} but current custodian is {holder!r}"
                )
            holder = event.to_custodian
