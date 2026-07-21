"""
BLACKSTAR LAW: DT Monitor
Hardware capacity sensing for Deterministic Throughput.
"""

from __future__ import annotations

from dataclasses import dataclass

from egregore.domain.units import DT


@dataclass(frozen=True, slots=True)
class DTReading:
    dt_available: DT
    dt_total: DT
    thermal_throttle: bool
    source: str


class DTMonitor:
    def read(self) -> DTReading:
        raise NotImplementedError


class StaticDTMonitor(DTMonitor):
    def __init__(self, total_dt: DT) -> None:
        self._total = total_dt

    def read(self) -> DTReading:
        return DTReading(
            dt_available=self._total,
            dt_total=self._total,
            thermal_throttle=False,
            source="static",
        )


class LinuxDTMonitor(DTMonitor):
    def __init__(self, total_dt: DT | None = None) -> None:
        self._total = total_dt or DT(9.0)

    def read(self) -> DTReading:
        try:
            with open("/proc/cpuinfo") as f:
                cpuinfo = f.read()
            cores = cpuinfo.count("processor\t:")
            bogomips = 0.0
            for line in cpuinfo.split("\n"):
                if "bogomips" in line.lower():
                    try:
                        bogomips = float(line.split(":")[1].strip())
                        break
                    except (ValueError, IndexError):
                        pass
            estimated_gflops = cores * bogomips * 0.001
            dt = DT.from_gflops(estimated_gflops)
            return DTReading(
                dt_available=dt,
                dt_total=self._total,
                thermal_throttle=False,
                source="linux_cpuinfo",
            )
        except Exception:
            return DTReading(
                dt_available=self._total,
                dt_total=self._total,
                thermal_throttle=False,
                source="linux_fallback",
            )
