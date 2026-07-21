"""Tests for hardware_profiler."""

from __future__ import annotations

from egregore.infrastructure import hardware_profiler as hp


def test_cpu_info_returns_values() -> None:
    info = hp.cpu_info()
    assert info.logical_cores > 0
    assert info.total_ram_bytes > 0


def test_hardware_snapshot_contains_cpu() -> None:
    snapshot = hp.hardware_snapshot()
    assert snapshot.cpu is not None
    assert snapshot.cpu.logical_cores > 0
