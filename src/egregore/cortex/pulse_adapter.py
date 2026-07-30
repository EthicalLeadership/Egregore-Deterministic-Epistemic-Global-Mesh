# epistemic marker: provenance / auditability
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from egregore.shared.canonical import canonical_json


class PublisherLike(Protocol):
    async def publish(self, subject: str, payload: bytes) -> Any: ...


@dataclass(frozen=True)
class PulseSample:
    ts_ns: int
    node_id: str
    cpu_pct: float
    gpu_temp_c: float
    gpu_power_mw: int
    vram_used: int
    vram_total: int


def build_pulse_payload(sample: PulseSample) -> bytes:
    # Stable JSON encoding for deterministic tests.
    payload = {
        "ts": sample.ts_ns,
        "node_id": sample.node_id,
        "cpu_pct": sample.cpu_pct,
        "gpu_temp": sample.gpu_temp_c,
        "gpu_power_mw": sample.gpu_power_mw,
        "vram_used": sample.vram_used,
        "vram_total": sample.vram_total,
    }
    return canonical_json(payload).encode("utf-8")


class PulseAdapter:
    """
    Publishes telemetry JSON to subjects: obs.pulse.<node_id>

    Dependency boundary:
    - No direct psutil/pynvml imports.
    - Telemetry collection is injected via callables so CPU-only tests remain deterministic.
    """

    def __init__(
        self,
        *,
        publisher: PublisherLike,
        node_id: str,
        get_ts_ns: Callable[[], int],
        get_cpu_pct: Callable[[], float],
        get_gpu_temp_c: Callable[[], float],
        get_gpu_power_mw: Callable[[], int],
        get_vram_used: Callable[[], int],
        get_vram_total: Callable[[], int],
    ) -> None:
        self._publisher = publisher
        self._node_id = node_id
        self._get_ts_ns = get_ts_ns
        self._get_cpu_pct = get_cpu_pct
        self._get_gpu_temp_c = get_gpu_temp_c
        self._get_gpu_power_mw = get_gpu_power_mw
        self._get_vram_used = get_vram_used
        self._get_vram_total = get_vram_total

    async def emit(self) -> bytes:
        sample = PulseSample(
            ts_ns=self._get_ts_ns(),
            node_id=self._node_id,
            cpu_pct=float(self._get_cpu_pct()),
            gpu_temp_c=float(self._get_gpu_temp_c()),
            gpu_power_mw=int(self._get_gpu_power_mw()),
            vram_used=int(self._get_vram_used()),
            vram_total=int(self._get_vram_total()),
        )
        payload = build_pulse_payload(sample)
        subject = f"obs.pulse.{self._node_id}"
        await self._publisher.publish(subject, payload)
        return payload
