from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any, Protocol

from egregore.infrastructure.telemetry.telemetry_collector import (
    Phase0GateMetrics,
    Phase0TelemetryCollector,
)


class PublisherLike(Protocol):
    async def publish(self, subject: str, payload: bytes) -> Any: ...


@dataclass(frozen=True)
class NetworkTotals:
    rx_bytes_total: int
    tx_bytes_total: int


class Phase0TelemetryPulseAgent:
    """
    Phase-0 telemetry agent (transport-agnostic).

    Emits canonical gate envelopes with deterministic timestamp_ns, matching the
    Phase-0 telemetry audit contract.
    """

    def __init__(
        self,
        *,
        publisher: PublisherLike,
        node_id: str,
        collector: Phase0TelemetryCollector | None = None,
        event_schema_version: str = "1.0.0",
        initial_event_seq: int = 0,
        dt_seconds: float = 1.0,
        cpu_sampler: Any | None = None,
    ) -> None:
        self._publisher = publisher
        self._node_id = node_id
        self._dt_seconds = float(dt_seconds)
        self._event_seq = int(initial_event_seq)
        self._event_schema_version = str(event_schema_version)

        self._cpu_sampler = cpu_sampler

        self._collector = collector or Phase0TelemetryCollector(
            node_id=node_id,
            event_schema_version=event_schema_version,
            get_gate_metrics=self._get_gate_metrics,
        )

    def _ensure_cpu_sampler(self) -> Any:
        if self._cpu_sampler is not None:
            return self._cpu_sampler
        from metrics.cpu_ram import CpuRamSampler

        self._cpu_sampler = CpuRamSampler()
        return self._cpu_sampler

    def _sample_cpu_memory_gate_metrics(
        self, *, dt_seconds: float
    ) -> Phase0GateMetrics:
        from os import getpid

        cpu_sampler = self._ensure_cpu_sampler()
        sample = cpu_sampler.sample(main_pids={getpid()}, dt_seconds=dt_seconds)
        return Phase0GateMetrics(
            cpu_pct=float(sample.cpu_total_pct), mem_used_pct=float(sample.mem_used_pct)
        )

    def _sample_gpu_gate_metrics(self) -> Phase0GateMetrics:
        from metrics.gpu import sample_gpu_row

        gpu_row = sample_gpu_row()
        return Phase0GateMetrics(
            gpu_util_pct=float(gpu_row.get("gpu_util_pct", 0.0)),
            gpu_mem_used_bytes=int(gpu_row.get("gpu_mem_used_bytes", 0)),
            gpu_mem_total_bytes=int(gpu_row.get("gpu_mem_total_bytes", 0)),
        )

    def _sample_storage_gate_metrics(self) -> Phase0GateMetrics:
        from metrics.disk_io import sample_disk_io_row

        disk_row = sample_disk_io_row()
        return Phase0GateMetrics(
            storage_r_s=float(disk_row.get("disk_r_s", 0.0)),
            storage_await_ms=float(disk_row.get("disk_await_ms", 0.0)),
        )

    def _sample_network_gate_metrics(self, *, dt_seconds: float) -> Phase0GateMetrics:
        _ = dt_seconds
        # Skeleton: keys present; values degrade deterministically to 0.0 for this repo snapshot.
        return Phase0GateMetrics(network_rx_bytes_s=0.0, network_tx_bytes_s=0.0)

    def _sample_interconnect_gate_metrics(self) -> Phase0GateMetrics:
        return Phase0GateMetrics(interconnect_bw_bytes_s=0.0)

    def _get_gate_metrics(self, gate: str) -> Phase0GateMetrics:
        if gate == "cpu":
            return self._sample_cpu_memory_gate_metrics(dt_seconds=self._dt_seconds)
        if gate == "memory":
            return self._sample_cpu_memory_gate_metrics(dt_seconds=self._dt_seconds)
        if gate == "storage":
            return self._sample_storage_gate_metrics()
        if gate == "network":
            return self._sample_network_gate_metrics(dt_seconds=self._dt_seconds)
        if gate == "gpu":
            return self._sample_gpu_gate_metrics()
        if gate == "interconnect":
            return self._sample_interconnect_gate_metrics()
        raise ValueError(f"Unknown gate: {gate!r}")

    async def emit_gate(self, *, gate: str) -> bytes:
        payload = self._collector.collect_gate_envelope_bytes(
            event_seq=self._event_seq, gate=gate
        )
        subject = f"obs.pulse.{self._node_id}"
        await self._publisher.publish(subject, payload)
        self._event_seq += 1
        return payload

    async def emit_all_gates(self) -> int:
        count = 0
        for gate in self._collector.gate_names():
            await self.emit_gate(gate=gate)
            count += 1
        return count

    async def emit_once(self) -> int:
        return await self.emit_all_gates()

    async def run(
        self,
        *,
        interval_sec: float = 1.0,
        run_seconds: float | None = None,
        sends_max: int | None = None,
    ) -> int:
        send_count = 0
        start_t = time.monotonic()

        while True:
            if run_seconds is not None and (time.monotonic() - start_t) >= float(
                run_seconds
            ):
                break
            if sends_max is not None and send_count >= int(sends_max):
                break

            with contextlib.suppress(Exception):
                emitted_payload_count = await self.emit_once()
                send_count += int(emitted_payload_count)

            await asyncio.sleep(max(0.01, float(interval_sec)))

        return send_count
