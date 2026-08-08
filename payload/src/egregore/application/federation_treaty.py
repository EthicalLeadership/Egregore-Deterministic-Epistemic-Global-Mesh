"""
Treaty lifecycle and persistence for federation alignment.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC
from typing import Any, Protocol

from egregore.domain.federation_constitution import Constitution, Treaty, TreatyState
from egregore.domain.provenance_model import ProvenanceEvent
from egregore.interface.provenance_port import IProvenanceSink
from egregore.shared.canonical import canonical_dumps, canonical_loads


class ITreatyStore(Protocol):
    def save(self, treaty: Treaty) -> None: ...
    def get(self, treaty_id: str) -> Treaty | None: ...
    def list_active(self) -> list[Treaty]: ...
    def add_signature(self, treaty_id: str, node_id: str, signature: str) -> None: ...
    def get_signatures(self, treaty_id: str) -> dict[str, str]: ...


def _serialize(treaty: Treaty) -> str:
    data = asdict(treaty)
    data["state"] = treaty.state.value
    return canonical_dumps(data, sort_keys=True)


def _deserialize(text: str | bytes) -> Treaty:
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    data = canonical_loads(text)
    data["state"] = TreatyState(data["state"])
    return Treaty(**data)


class InMemoryTreatyStore:
    """Fallback treaty store for testing and offline operation."""

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


class RedisTreatyStore:
    """Redis-backed treaty store."""

    KEY_PREFIX = "egregore:treaty"
    ACTIVE_SET = "egregore:treaties:active"
    NODE_SET_PREFIX = "egregore:treaties:node"

    def __init__(self, redis_client: Any) -> None:
        self._r = redis_client

    def _key(self, treaty_id: str) -> str:
        return f"{self.KEY_PREFIX}:{treaty_id}"

    def _node_key(self, node_id: str) -> str:
        return f"{self.NODE_SET_PREFIX}:{node_id}"

    def save(self, treaty: Treaty) -> None:
        self._r.set(self._key(treaty.treaty_id), _serialize(treaty))
        if treaty.state == TreatyState.ACTIVE:
            self._r.sadd(self.ACTIVE_SET, treaty.treaty_id)

    def get(self, treaty_id: str) -> Treaty | None:
        raw = self._r.get(self._key(treaty_id))
        if raw is None:
            return None
        return _deserialize(raw)

    def list_active(self) -> list[Treaty]:
        ids = self._r.smembers(self.ACTIVE_SET)
        ids = [i.decode() if isinstance(i, bytes) else i for i in ids]
        return [t for t in (self.get(i) for i in ids) if t is not None]

    def add_signature(self, treaty_id: str, node_id: str, signature: str) -> None:
        self._r.hset(f"{self.KEY_PREFIX}:{treaty_id}:signatures", node_id, signature)
        self._r.sadd(self._node_key(node_id), treaty_id)

    def get_signatures(self, treaty_id: str) -> dict[str, str]:
        raw = self._r.hgetall(f"{self.KEY_PREFIX}:{treaty_id}:signatures")
        result: dict[str, str] = {}
        for k, v in raw.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            result[key] = val
        return result


class TreatyLedger:
    """
    Manages the lifecycle of federation treaties and appends constitutional
    events to the .zarc provenance chain.
    """

    def __init__(
        self,
        constitution: Constitution,
        store: ITreatyStore,
        provenance_sink: IProvenanceSink | None = None,
    ) -> None:
        self._constitution = constitution
        self._store = store
        self._provenance = provenance_sink

    def propose(
        self,
        treaty_id: str,
        parties: list[str],
        clauses: list[str],
    ) -> Treaty:
        if self._constitution.required_clauses() - set(clauses):
            raise ValueError("treaty missing required constitutional clauses")
        treaty = Treaty(
            treaty_id=treaty_id,
            parties=tuple(parties),
            clauses=tuple(clauses),
            state=TreatyState.PROPOSED,
            proposed_at=datetime_now_iso(),
        )
        self._store.save(treaty)
        self._emit(
            "treaty_proposed",
            {"treaty_id": treaty_id, "parties": parties, "clauses": clauses},
        )
        return treaty

    def ratify(self, treaty_id: str, node_id: str, signature: str) -> Treaty:
        treaty = self._store.get(treaty_id)
        if treaty is None:
            raise KeyError(f"treaty not found: {treaty_id}")
        if node_id not in treaty.parties:
            raise ValueError(f"node {node_id} is not a party to treaty {treaty_id}")
        self._store.add_signature(treaty_id, node_id, signature)
        signatures = self._collect_signatures(treaty_id)
        if (
            all(p in signatures for p in treaty.parties)
            and treaty.state != TreatyState.ACTIVE
        ):
            treaty = Treaty(
                treaty_id=treaty.treaty_id,
                parties=treaty.parties,
                clauses=treaty.clauses,
                state=TreatyState.ACTIVE,
                proposed_at=treaty.proposed_at,
                ratified_at=datetime_now_iso(),
                signatures=signatures,
            )
            self._store.save(treaty)
            self._emit(
                "treaty_ratified",
                {
                    "treaty_id": treaty_id,
                    "parties": list(treaty.parties),
                    "signatures": {k: f"{v[:16]}..." for k, v in signatures.items()},
                },
            )
        return treaty

    def active_treaty(self) -> Treaty | None:
        active = self._store.list_active()
        return active[0] if active else None

    def has_ratified(self, node_id: str, treaty_id: str | None = None) -> bool:
        if treaty_id is None:
            active = self.active_treaty()
            if active is None:
                return False
            treaty_id = active.treaty_id
        treaty = self._store.get(treaty_id)
        if treaty is None:
            return False
        sigs = self._collect_signatures(treaty_id)
        return node_id in sigs and treaty.state == TreatyState.ACTIVE

    def _collect_signatures(self, treaty_id: str) -> dict[str, str]:
        return self._store.get_signatures(treaty_id)

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._provenance is None:
            return
        self._provenance.append(
            ProvenanceEvent(
                engine="federation",
                event=event,
                payload=payload,
                ts_ns=time.time_ns(),
            )
        )


def datetime_now_iso() -> str:
    from datetime import datetime

    return datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC).isoformat()
