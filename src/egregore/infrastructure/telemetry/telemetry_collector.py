from __future__ import annotations

import hashlib
import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

_mod = importlib.import_module("egregore.shared.canonical")
canonical_json = _mod.canonical_json


def _sha256_int64_hex_prefix(s: str) -> int:
    """
    Deterministically derive an int timestamp-like value from stable inputs.

    Returns a positive int64 derived from the first 16 hex chars of SHA256(s).
    """
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
    v = int(h, 16)
    return v if v >= 0 else -v


def derive_deterministic_timestamp_ns(
    *, node_id: str, event_seq: int, gate: str
) -> int:
    """
    Gate-5 deterministic timestamp derivation.

    This implementation MUST NOT use wall-clock time. It derives timestamp_ns
    purely from stable identifiers.
    """
    stable = (
        f"telemetry.timestamp_ns|node_id={node_id}|event_seq={event_seq}|gate={gate}"
    )
    return _sha256_int64_hex_prefix(stable)


@dataclass(frozen=True)
class TelemetryEnvelope:
    """
    Canonical telemetry envelope for Phase-0 audit contract.

    Required keys (per audit narrative):
    - timestamp_ns
    - event_schema_version
    - event_seq
    - node_id
    - event_type
    - gate
    - metrics (object)

    The JSON encoding is canonical via egregore.shared.canonical.canonical_json().
    """

    timestamp_ns: int
    event_schema_version: str
    event_seq: int

    node_id: str
    event_type: str
    gate: str

    metrics: Mapping[str, Any]

    def to_payload_bytes(self) -> bytes:
        payload = {
            "timestamp_ns": int(self.timestamp_ns),
            "event_schema_version": str(self.event_schema_version),
            "event_seq": int(self.event_seq),
            "node_id": str(self.node_id),
            "event_type": str(self.event_type),
            "gate": str(self.gate),
            "metrics": dict(self.metrics),
        }
        return canonical_json(payload).encode("utf-8")


@dataclass(frozen=True)
class Phase0GateMetrics:
    """
    Minimal metrics set to satisfy “coverage exists” across gates.

    Even when an underlying signal is best-effort in this CPU-only skeleton,
    the envelope still includes stable keys, and values degrade deterministically.
    """

    cpu_pct: float = 0.0
    mem_used_pct: float = 0.0
    storage_r_s: float = 0.0
    storage_await_ms: float = 0.0
    network_rx_bytes_s: float = 0.0
    network_tx_bytes_s: float = 0.0
    gpu_util_pct: float = 0.0
    gpu_mem_used_bytes: int = 0
    gpu_mem_total_bytes: int = 0
    interconnect_bw_bytes_s: float = 0.0


class Phase0TelemetryCollector:
    """
    Phase-0 telemetry collector that emits one canonical envelope per gate.

    This collector is intentionally transport-agnostic:
    - It returns payload bytes; the caller is responsible for publishing them.
    """

    def __init__(
        self,
        *,
        node_id: str,
        event_type: str = "telemetry.gate.all",
        event_schema_version: str = "1.0.0",
        get_gate_metrics: Callable[[str], Phase0GateMetrics] | None = None,
    ) -> None:
        self._node_id = node_id
        self._event_type = event_type
        self._event_schema_version = event_schema_version
        self._get_gate_metrics = get_gate_metrics

        self._gates = (
            "cpu",
            "memory",
            "storage",
            "network",
            "gpu",
            "interconnect",
        )

    def _default_get_gate_metrics(self, gate: str) -> Phase0GateMetrics:
        # CPU-only skeleton: stable keys only, values degrade deterministically.
        _ = gate
        return Phase0GateMetrics()

    def collect_gate_envelope_bytes(
        self,
        *,
        event_seq: int,
        gate: str,
    ) -> bytes:
        if gate not in set(self._gates):
            raise ValueError(f"Unknown Phase0 gate: {gate!r}")

        metrics_fn = self._get_gate_metrics or self._default_get_gate_metrics
        metrics = metrics_fn(gate)

        metrics_dict = {
            "cpu_pct": float(metrics.cpu_pct),
            "mem_used_pct": float(metrics.mem_used_pct),
            "storage_r_s": float(metrics.storage_r_s),
            "storage_await_ms": float(metrics.storage_await_ms),
            "network_rx_bytes_s": float(metrics.network_rx_bytes_s),
            "network_tx_bytes_s": float(metrics.network_tx_bytes_s),
            "gpu_util_pct": float(metrics.gpu_util_pct),
            "gpu_mem_used_bytes": int(metrics.gpu_mem_used_bytes),
            "gpu_mem_total_bytes": int(metrics.gpu_mem_total_bytes),
            "interconnect_bw_bytes_s": float(metrics.interconnect_bw_bytes_s),
        }

        timestamp_ns = derive_deterministic_timestamp_ns(
            node_id=self._node_id,
            event_seq=event_seq,
            gate=gate,
        )

        envelope = TelemetryEnvelope(
            timestamp_ns=timestamp_ns,
            event_schema_version=self._event_schema_version,
            event_seq=int(event_seq),
            node_id=self._node_id,
            event_type=self._event_type,
            gate=gate,
            metrics=metrics_dict,
        )
        return envelope.to_payload_bytes()

    def gate_names(self) -> tuple[str, ...]:
        return self._gates
