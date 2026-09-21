from egregore.domain.units import DT
from egregore.kernel.scheduler.dt_monitor import LinuxDTMonitor, StaticDTMonitor


class TestStaticDTMonitor:
    def test_read(self):
        monitor = StaticDTMonitor(DT(10.0))
        reading = monitor.read()
        assert reading.dt_available == DT(10.0)
        assert reading.thermal_throttle is False
        assert reading.source == "static"

    def test_read_custom(self):
        monitor = StaticDTMonitor(DT(5.5))
        reading = monitor.read()
        assert reading.dt_available == DT(5.5)


class TestLinuxDTMonitor:
    def test_read_fallback(self):
        monitor = LinuxDTMonitor(DT(9.0))
        reading = monitor.read()
        assert reading.dt_total == DT(9.0)
        assert reading.source in ("linux_cpuinfo", "linux_fallback")
