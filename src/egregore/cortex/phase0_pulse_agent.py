from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from egregore.cortex.pulse_adapter import PublisherLike, PulseAdapter


def default_get_ts_ns() -> int:
    """
    CLOCK_MONOTONIC_RAW nanoseconds when available; otherwise fall back to wall-clock.
    """
    try:
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    except Exception:
        return time.time_ns()


@dataclass(frozen=True)
class Phase0PulseSample:
    cpu_pct: float
    gpu_temp_c: float
    gpu_power_mw: int
    vram_used_mb: int
    vram_total_mb: int


def _bytes_to_mb_floor(b: int) -> int:
    if b <= 0:
        return 0
    return int(b // (1024 * 1024))


def sample_phase0_pulse_metrics(
    *,
    cpu_sampler: Any,
    dt_seconds: float,
) -> Phase0PulseSample:
    """
    CPU-first sampling using injected `cpu_sampler` and best-effort GPU row.

    - cpu_sampler is expected to implement .sample(main_pids, dt_seconds) and expose
      cpu_total_pct and mem_used_pct (see src/metrics/cpu_ram.py).
    - GPU sampling degrades to zeros when pynvml/nvidia-smi are absent.
    """
    # Use the current process as a stable "main_pids" set for delta accounting.
    main_pids: set[int] = {os.getpid()}

    cpu_row = cpu_sampler.sample(main_pids=main_pids, dt_seconds=dt_seconds)
    cpu_pct = float(cpu_row.cpu_total_pct)

    # GPU sampler is intentionally optional/best-effort.
    from metrics.gpu import sample_gpu_row

    gpu_row = sample_gpu_row()
    vram_used_mb = _bytes_to_mb_floor(int(gpu_row.get("gpu_mem_used_bytes", 0)))
    vram_total_mb = _bytes_to_mb_floor(int(gpu_row.get("gpu_mem_total_bytes", 0)))

    # Repo metrics currently does not expose temperature/power reliably in the Phase-0 pipeline.
    # Degrade deterministically to 0 so the envelope shape remains stable.
    return Phase0PulseSample(
        cpu_pct=cpu_pct,
        gpu_temp_c=0.0,
        gpu_power_mw=0,
        vram_used_mb=vram_used_mb,
        vram_total_mb=vram_total_mb,
    )


class Phase0PulseTelemetryAgent:
    """
    Minimal Phase-0 pulse telemetry agent.

    Contract:
    - Dependency-injected publisher (no hard NATS dependency).
    - Emits a single pulse payload via PulseAdapter.emit().
    - Optional bounded run loop for systemd usage (run_seconds / sends_max).
    """

    def __init__(
        self,
        *,
        publisher: PublisherLike,
        node_id: str,
        get_ts_ns: Callable[[], int] = default_get_ts_ns,
        cpu_sampler: Any | None = None,
        dt_seconds: float = 1.0,
    ) -> None:
        self._publisher = publisher
        self._node_id = node_id
        self._get_ts_ns = get_ts_ns
        self._cpu_sampler = (
            cpu_sampler if cpu_sampler is not None else _default_cpu_sampler()
        )
        self._dt_seconds = float(dt_seconds)

    async def emit_once(self) -> bytes:
        ts_ns = int(self._get_ts_ns())

        sample = sample_phase0_pulse_metrics(
            cpu_sampler=self._cpu_sampler,
            dt_seconds=self._dt_seconds,
        )

        adapter = PulseAdapter(
            publisher=self._publisher,
            node_id=self._node_id,
            get_ts_ns=lambda: ts_ns,
            get_cpu_pct=lambda: sample.cpu_pct,
            get_gpu_temp_c=lambda: sample.gpu_temp_c,
            get_gpu_power_mw=lambda: sample.gpu_power_mw,
            get_vram_used=lambda: sample.vram_used_mb,
            get_vram_total=lambda: sample.vram_total_mb,
        )
        return await adapter.emit()

    async def run(
        self,
        *,
        interval_sec: float = 1.0,
        run_seconds: float | None = None,
        sends_max: int | None = None,
    ) -> int:
        """
        Run the agent loop; returns number of successful emits.

        Failure semantics:
        - Any exception during emit_once is treated as transient; the loop continues.
        """
        send_count = 0
        start = time.time()

        while True:
            if run_seconds is not None and (time.time() - start) >= float(run_seconds):
                break
            if sends_max is not None and send_count >= int(sends_max):
                break

            with contextlib.suppress(Exception):
                _ = await self.emit_once()
                send_count += 1

            await self._sleep_bounded(interval_sec=interval_sec)

        return send_count

    async def _sleep_bounded(self, *, interval_sec: float) -> None:
        interval_sec = max(0.01, float(interval_sec))
        await self._async_sleep(interval_sec)

    async def _async_sleep(self, interval_sec: float) -> None:
        import asyncio

        await asyncio.sleep(interval_sec)


def _default_cpu_sampler() -> Any:
    from metrics.cpu_ram import CpuRamSampler

    return CpuRamSampler()
