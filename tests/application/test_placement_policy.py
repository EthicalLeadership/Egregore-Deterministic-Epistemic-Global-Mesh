"""Tests for placement_policy."""

from __future__ import annotations

from egregore.application.placement_policy import decide_placement
from egregore.infrastructure.hardware_profiler import (
    CpuInfo,
    GpuInfo,
    HardwareSnapshot,
)


def test_no_gpu_chooses_cpu() -> None:
    hw = HardwareSnapshot(
        cpu=CpuInfo(
            physical_cores=4,
            logical_cores=8,
            total_ram_bytes=16e9,
            available_ram_bytes=8e9,
        ),
        gpus=[],
    )
    decision = decide_placement(model_size_bytes=1_000_000_000, hardware=hw)
    assert decision.n_gpu_layers == 0
    assert "No GPU" in decision.reason


def test_gpu_with_enough_vram_full_offload() -> None:
    gpu = GpuInfo(index=0, name="RTX 3060", total_vram_bytes=12e9, free_vram_bytes=10e9)
    hw = HardwareSnapshot(
        cpu=CpuInfo(
            physical_cores=4,
            logical_cores=8,
            total_ram_bytes=16e9,
            available_ram_bytes=8e9,
        ),
        gpus=[gpu],
    )
    decision = decide_placement(model_size_bytes=1_000_000_000, hardware=hw)
    assert decision.n_gpu_layers == -1
    assert "full offload" in decision.reason


def test_gpu_with_low_vram_chooses_cpu() -> None:
    gpu = GpuInfo(
        index=0, name="RTX 3060", total_vram_bytes=12e9, free_vram_bytes=100e6
    )
    hw = HardwareSnapshot(
        cpu=CpuInfo(
            physical_cores=4,
            logical_cores=8,
            total_ram_bytes=16e9,
            available_ram_bytes=8e9,
        ),
        gpus=[gpu],
    )
    decision = decide_placement(model_size_bytes=2_000_000_000, hardware=hw)
    assert decision.n_gpu_layers == 0
    assert "CPU-only" in decision.reason
