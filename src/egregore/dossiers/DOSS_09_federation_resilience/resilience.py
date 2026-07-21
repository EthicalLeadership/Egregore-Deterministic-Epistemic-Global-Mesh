"""DOSS-09: Federation & Resilience — Treaty lifecycle and resilience patterns."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC
from enum import StrEnum
from typing import Protocol


class TreatyState(StrEnum):
    PROPOSED = "PROPOSED"
    RATIFIED = "RATIFIED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class Treaty:
    treaty_id: str
    parties: tuple[str, ...]
    clauses: tuple[str, ...]
    state: TreatyState
    proposed_at: str
    ratified_at: str | None = None
    expires_at: str | None = None
    signatures: dict[str, str] = field(default_factory=dict)


class ITreatyStore(Protocol):
    def save(self, treaty: Treaty) -> None: ...
    def get(self, treaty_id: str) -> Treaty | None: ...
    def list_active(self) -> list[Treaty]: ...
    def add_signature(self, treaty_id: str, node_id: str, signature: str) -> None: ...
    def get_signatures(self, treaty_id: str) -> dict[str, str]: ...


class InMemoryTreatyStore:
    def __init__(self) -> None:
        self._treaties: dict[str, Treaty] = {}
        self._signatures: dict[str, dict[str, str]] = {}

    def save(self, treaty: Treaty) -> None:
        self._treaties[treaty.treaty_id] = treaty
        self._signatures.setdefault(treaty.treaty_id, {})

    def get(self, treaty_id: str) -> Treaty | None:
        return self._treaties.get(treaty_id)

    def list_active(self) -> list[Treaty]:
        return [t for t in self._treaties.values() if t.state == TreatyState.ACTIVE]

    def add_signature(self, treaty_id: str, node_id: str, signature: str) -> None:
        self._signatures.setdefault(treaty_id, {})[node_id] = signature

    def get_signatures(self, treaty_id: str) -> dict[str, str]:
        return dict(self._signatures.get(treaty_id, {}))


class FederationResilience:
    """Federation treaty lifecycle and resilience manager for Egregore."""

    def __init__(self, store: ITreatyStore) -> None:
        self._store = store

    def propose(self, treaty_id: str, parties: list[str], clauses: list[str]) -> Treaty:
        treaty = Treaty(
            treaty_id=treaty_id,
            parties=tuple(parties),
            clauses=tuple(clauses),
            state=TreatyState.PROPOSED,
            proposed_at=self._now(),
        )
        self._store.save(treaty)
        return treaty

    def ratify(self, treaty_id: str, node_id: str, signature: str) -> Treaty | None:
        treaty = self._store.get(treaty_id)
        if treaty is None:
            return None
        if node_id not in treaty.parties:
            raise ValueError(f"Node {node_id} is not a party to treaty {treaty_id}")
        self._store.add_signature(treaty_id, node_id, signature)
        sigs = self._store.get_signatures(treaty_id)
        if (
            all(p in sigs for p in treaty.parties)
            and treaty.state != TreatyState.ACTIVE
        ):
            active_treaty = Treaty(
                treaty_id=treaty.treaty_id,
                parties=treaty.parties,
                clauses=treaty.clauses,
                state=TreatyState.ACTIVE,
                proposed_at=treaty.proposed_at,
                ratified_at=self._now(),
                signatures=sigs,
            )
            self._store.save(active_treaty)
            return active_treaty
        return treaty

    def active_treaties(self) -> list[Treaty]:
        return self._store.list_active()

    def active_treaty(self) -> Treaty | None:
        active = self._store.list_active()
        return active[0] if active else None

    def has_ratified(self, node_id: str, treaty_id: str) -> bool:
        treaty = self._store.get(treaty_id)
        if treaty is None or treaty.state != TreatyState.ACTIVE:
            return False
        sigs = self._store.get_signatures(treaty_id)
        return node_id in sigs

    def _now(self) -> str:
        from datetime import datetime

        return datetime.now(UTC).isoformat()
