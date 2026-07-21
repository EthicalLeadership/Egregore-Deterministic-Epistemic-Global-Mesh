"""Hardware profiler — detect CPU, RAM, and GPU resources for the orchestrator."""

from __future__ import annotations

import contextlib
import os
import subprocess
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    total_vram_bytes: int
    free_vram_bytes: int


@dataclass(frozen=True)
class CpuInfo:
    physical_cores: int
    logical_cores: int
    total_ram_bytes: int
    available_ram_bytes: int


@dataclass(frozen=True)
class HardwareSnapshot:
    cpu: CpuInfo
    gpus: list[GpuInfo] = field(default_factory=list)


def _parse_size(size_str: str) -> int:
    """Parse nvidia-smi MiB/GiB strings into bytes."""
    size_str = size_str.strip().lower().replace(",", "")
    if size_str.endswith("mib"):
        return int(float(size_str[:-3].strip()) * 1024 * 1024)
    if size_str.endswith("gib"):
        return int(float(size_str[:-3].strip()) * 1024 * 1024 * 1024)
    if size_str.endswith("mb"):
        return int(float(size_str[:-2].strip()) * 1000 * 1000)
    if size_str.endswith("gb"):
        return int(float(size_str[:-2].strip()) * 1000 * 1000 * 1000)
    return int(size_str)


def _gpu_info_via_smi() -> list[GpuInfo]:
    """Parse nvidia-smi for GPU memory info."""
    try:
        result = subprocess.run(
            [  # noqa: S607
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.free",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        gpus: list[GpuInfo] = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                continue
            gpus.append(
                GpuInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    total_vram_bytes=_parse_size(parts[2]),
                    free_vram_bytes=_parse_size(parts[3]),
                )
            )
        return gpus
    except Exception:
        return []


def _gpu_info_via_pynvml() -> list[GpuInfo]:
    try:
        # pynvml is an optional dependency; if absent this path falls back to nvidia-smi parsing.
        import pynvml  # type: ignore[import-not-found]

        pynvml.nvmlInit()
        gpus: list[GpuInfo] = []
        for i in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gpus.append(
                GpuInfo(
                    index=i,
                    name=name.decode("utf-8") if isinstance(name, bytes) else name,
                    total_vram_bytes=mem.total,
                    free_vram_bytes=mem.free,
                )
            )
        return gpus
    except Exception:
        return []


def cpu_info() -> CpuInfo:
    """Return CPU and RAM information."""
    try:
        # psutil is an optional dependency; if absent this path falls back to /proc/meminfo.
        import psutil  # type: ignore[import-not-found]

        return CpuInfo(
            physical_cores=psutil.cpu_count(logical=False) or 1,
            logical_cores=psutil.cpu_count(logical=True) or 1,
            total_ram_bytes=psutil.virtual_memory().total,
            available_ram_bytes=psutil.virtual_memory().available,
        )
    except Exception:
        # Fallback to /proc on Linux.
        logical = os.cpu_count() or 1
        total_ram = 0
        available_ram = 0
        with contextlib.suppress(Exception), open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_ram = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    available_ram = int(line.split()[1]) * 1024
        return CpuInfo(
            physical_cores=logical,
            logical_cores=logical,
            total_ram_bytes=total_ram,
            available_ram_bytes=available_ram,
        )


def gpu_info() -> list[GpuInfo]:
    """Return GPU information, preferring pynvml over nvidia-smi parsing."""
    gpus = _gpu_info_via_pynvml()
    if gpus:
        return gpus
    return _gpu_info_via_smi()


def hardware_snapshot() -> HardwareSnapshot:
    """Return a deterministic snapshot of current hardware resources."""
    return HardwareSnapshot(cpu=cpu_info(), gpus=gpu_info())
