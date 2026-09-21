"""EGREGORE LAW: Cluster Aggregator. Pioneer 1 + Pioneer 2."""

from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass
from typing import Any

from egregore.domain.units import DT, TU
from egregore.shared.canonical import canonical_dumps, canonical_loads

_NodeTrustStore = None
_TreatyLedger = None


def _node_store_cls():
    global _NodeTrustStore
    if _NodeTrustStore is None:
        from egregore.infrastructure.redis_store import (
            NodeTrustStore as _node_trust_store,  # noqa: N813
        )

        _NodeTrustStore = _node_trust_store
    return _NodeTrustStore


def _treaty_ledger_cls():
    global _TreatyLedger
    if _TreatyLedger is None:
        from egregore.application.federation_treaty import (
            TreatyLedger as _treaty_ledger,  # noqa: N813
        )

        _TreatyLedger = _treaty_ledger
    return _TreatyLedger


@dataclass(frozen=True, slots=True)
class NodeCapacity:
    node_id: str
    available_dt: DT
    available_tu: TU
    total_dt: DT
    total_tu: TU
    thermal_throttle: bool
    last_seen_ns: int


class ClusterAggregator:
    def __init__(
        self,
        node_store: Any = None,
        treaty_ledger: Any = None,
    ) -> None:
        self._nodes: dict[str, NodeCapacity] = {}
        self._node_store = node_store
        self._treaty_ledger = treaty_ledger
        if self._node_store is not None:
            self._load_nodes()

    def register_node(self, capacity: NodeCapacity) -> None:
        if self._treaty_ledger is not None:
            tl = self._treaty_ledger
            active = tl.active_treaty()
            if active is not None and not tl.has_ratified(capacity.node_id):
                raise ValueError(
                    f"Node {capacity.node_id} has not ratified active treaty {active.treaty_id}"
                )
        self._nodes[capacity.node_id] = capacity
        if self._node_store is not None:
            self._persist_node(capacity)

    def remove_node(self, node_id: str) -> None:
        if node_id in self._nodes:
            del self._nodes[node_id]
        if self._node_store is not None:
            self._node_store.delete(f"egregore:cluster:node:{node_id}")
            self._node_store.srem("egregore:cluster:node_ids", node_id)

    def get_cluster_capacity(self) -> dict:
        if not self._nodes:
            return {
                "status": "NO_NODES",
                "total_dt": DT(0).to_canonical(),
                "total_tu": TU(0).to_canonical(),
            }
        total_dt = sum(n.available_dt.value for n in self._nodes.values())
        total_tu = sum(n.available_tu.value for n in self._nodes.values())
        return {
            "status": "OK",
            "node_count": len(self._nodes),
            "total_dt": DT(total_dt).to_canonical(),
            "total_tu": TU(total_tu).to_canonical(),
            "nodes": [n.node_id for n in self._nodes.values()],
        }

    def get_node(self, node_id: str) -> NodeCapacity | None:
        return self._nodes.get(node_id)

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    def _persist_node(self, capacity: NodeCapacity) -> None:
        if self._node_store is None:
            return
        data = asdict(capacity)
        data["available_dt"] = capacity.available_dt.to_canonical()
        data["total_dt"] = capacity.total_dt.to_canonical()
        data["available_tu"] = capacity.available_tu.to_canonical()
        data["total_tu"] = capacity.total_tu.to_canonical()
        self._node_store.set(
            f"egregore:cluster:node:{capacity.node_id}",
            canonical_dumps(data, sort_keys=True),
        )
        self._node_store.sadd("egregore:cluster:node_ids", capacity.node_id)

    def _load_nodes(self) -> None:
        if self._node_store is None:
            return
        node_ids = self._node_store.smembers("egregore:cluster:node_ids")
        node_ids = [n.decode() if isinstance(n, bytes) else n for n in node_ids]
        for node_id in node_ids:
            raw = self._node_store.get(f"egregore:cluster:node:{node_id}")
            if raw is None:
                self._node_store.srem("egregore:cluster:node_ids", node_id)
                continue
            with contextlib.suppress(Exception):
                self._nodes[node_id] = _deserialize_node_capacity(raw)


def _deserialize_node_capacity(raw: str | bytes) -> NodeCapacity:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = canonical_loads(raw)
    return NodeCapacity(
        node_id=data["node_id"],
        available_dt=_tu_or_dt_from_canonical(data["available_dt"], DT),
        available_tu=_tu_or_dt_from_canonical(data["available_tu"], TU),
        total_dt=_tu_or_dt_from_canonical(data["total_dt"], DT),
        total_tu=_tu_or_dt_from_canonical(data["total_tu"], TU),
        thermal_throttle=data["thermal_throttle"],
        last_seen_ns=data["last_seen_ns"],
    )


def _tu_or_dt_from_canonical(data: dict, cls: Any) -> Any:
    if data.get("__type__") == "DT":
        return DT(data["value"])
    if data.get("__type__") == "TU":
        return TU(data["value"])
    return cls(data["value"])
