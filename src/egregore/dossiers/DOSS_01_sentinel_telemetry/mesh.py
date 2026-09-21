"""DOSS-01: Sentinel Telemetry Mesh — Telemetry collection, pulse agents, and observability pipeline."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class TelemetryPublisher(Protocol):
    def publish(self, subject: str, payload: bytes) -> Any: ...


@dataclass
class PulseSample:
    ts_ns: int
    node_id: str
    cpu_pct: float
    gpu_temp_c: float
    gpu_power_mw: int
    vram_used: int
    vram_total: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "ts": self.ts_ns,
                "node_id": self.node_id,
                "cpu_pct": self.cpu_pct,
                "gpu_temp_c": self.gpu_temp_c,
                "gpu_power_mw": self.gpu_power_mw,
                "vram_used": self.vram_used,
                "vram_total": self.vram_total,
            },
            sort_keys=True,
        )


@dataclass
class TelemetryCollector:
    samples: list[PulseSample] = field(default_factory=list)
    _callbacks: list[Callable[[PulseSample], None]] = field(default_factory=list)
    _max_samples: int = 10000

    def ingest(self, sample: PulseSample) -> None:
        self.samples.append(sample)
        # Automatic eviction: keep only the most recent window
        if len(self.samples) > self._max_samples:
            self.samples = self.samples[-self._max_samples :]
        for cb in self._callbacks:
            cb(sample)

    def on_sample(self, callback: Callable[[PulseSample], None]) -> None:
        self._callbacks.append(callback)

    def snapshot(self, window: int | None = None) -> dict[str, Any]:
        if not self.samples:
            return {"sources": 0}
        windowed = self.samples[-window:] if window else self.samples
        cpus = [s.cpu_pct for s in windowed]
        avg_cpu = sum(cpus) / len(cpus)
        return {
            "sources": len({s.node_id for s in windowed}),
            "avg_cpu": round(avg_cpu, 2),
            "max_cpu": round(max(cpus), 2),
            "min_cpu": round(min(cpus), 2),
            "sample_count": len(windowed),
            "latest_ts_ns": max(s.ts_ns for s in windowed),
        }

    def clear(self) -> None:
        self.samples.clear()


class SentinelTelemetryMesh:
    """Central telemetry mesh coordinator for the Egregore sentinel layer."""

    def __init__(
        self, node_id: str, publisher: TelemetryPublisher | None = None
    ) -> None:
        self.node_id = node_id
        self.publisher = publisher
        self.collector = TelemetryCollector()

    def emit_pulse(self, sample: PulseSample) -> None:
        if self.publisher is not None:
            self.publisher.publish("telemetry.pulse", sample.to_json().encode())
        self.collector.ingest(sample)

    def health(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": "healthy",
            "samples_collected": len(self.collector.samples),
        }
