"""Tests for DOSS-01: Sentinel Telemetry Mesh."""

from __future__ import annotations

from egregore.dossiers.DOSS_01_sentinel_telemetry.mesh import (
    PulseSample,
    SentinelTelemetryMesh,
    TelemetryCollector,
)


class MockPublisher:
    def __init__(self):
        self.messages = []

    def publish(self, subject: str, payload: bytes) -> None:
        self.messages.append((subject, payload))


def test_pulse_sample_serializes_to_json():
    sample = PulseSample(
        ts_ns=1234567890000000000,
        node_id="pioneer-1",
        cpu_pct=45.5,
        gpu_temp_c=72.0,
        gpu_power_mw=199400,
        vram_used=3900,
        vram_total=8192,
    )
    json_str = sample.to_json()
    assert "pioneer-1" in json_str
    assert "cpu_pct" in json_str


def test_telemetry_collector_aggregates_samples():
    collector = TelemetryCollector()
    sample1 = PulseSample(
        ts_ns=1,
        node_id="node-1",
        cpu_pct=50.0,
        gpu_temp_c=70.0,
        gpu_power_mw=200000,
        vram_used=4000,
        vram_total=8192,
    )
    sample2 = PulseSample(
        ts_ns=2,
        node_id="node-2",
        cpu_pct=30.0,
        gpu_temp_c=60.0,
        gpu_power_mw=150000,
        vram_used=3000,
        vram_total=8192,
    )
    collector.ingest(sample1)
    collector.ingest(sample2)

    snapshot = collector.snapshot()
    assert snapshot["sources"] == 2
    assert snapshot["avg_cpu"] == 40.0
    assert snapshot["max_cpu"] == 50.0
    assert snapshot["min_cpu"] == 30.0
    assert snapshot["sample_count"] == 2


def test_telemetry_collector_windowed_snapshot():
    collector = TelemetryCollector()
    for i in range(10):
        collector.ingest(
            PulseSample(
                ts_ns=i,
                node_id="node-1",
                cpu_pct=float(i * 10),
                gpu_temp_c=70.0,
                gpu_power_mw=200000,
                vram_used=4000,
                vram_total=8192,
            )
        )
    # Window of last 3 samples: cpu_pct = 70, 80, 90
    snap = collector.snapshot(window=3)
    assert snap["avg_cpu"] == 80.0
    assert snap["max_cpu"] == 90.0
    assert snap["min_cpu"] == 70.0
    assert snap["sample_count"] == 3


def test_telemetry_collector_eviction():
    collector = TelemetryCollector(_max_samples=5)
    for i in range(10):
        collector.ingest(
            PulseSample(
                ts_ns=i,
                node_id="node-1",
                cpu_pct=float(i),
                gpu_temp_c=70.0,
                gpu_power_mw=200000,
                vram_used=4000,
                vram_total=8192,
            )
        )
    assert len(collector.samples) == 5
    # Should only retain the last 5: cpu_pct = 5,6,7,8,9
    snap = collector.snapshot()
    assert snap["avg_cpu"] == 7.0


def test_telemetry_collector_callback():
    collector = TelemetryCollector()
    received = []
    collector.on_sample(lambda s: received.append(s.node_id))
    sample = PulseSample(
        ts_ns=1,
        node_id="node-x",
        cpu_pct=10.0,
        gpu_temp_c=70.0,
        gpu_power_mw=200000,
        vram_used=4000,
        vram_total=8192,
    )
    collector.ingest(sample)
    assert received == ["node-x"]


def test_telemetry_collector_empty_snapshot():
    collector = TelemetryCollector()
    snap = collector.snapshot()
    assert snap["sources"] == 0


def test_sentinel_mesh_emits_pulse():
    publisher = MockPublisher()
    mesh = SentinelTelemetryMesh(node_id="pioneer-1", publisher=publisher)

    sample = PulseSample(
        ts_ns=1,
        node_id="pioneer-1",
        cpu_pct=50.0,
        gpu_temp_c=70.0,
        gpu_power_mw=200000,
        vram_used=4000,
        vram_total=8192,
    )
    mesh.emit_pulse(sample)

    assert len(publisher.messages) == 1
    assert publisher.messages[0][0] == "telemetry.pulse"


def test_sentinel_mesh_health():
    mesh = SentinelTelemetryMesh(node_id="pioneer-1")
    health = mesh.health()
    assert health["node_id"] == "pioneer-1"
    assert health["status"] == "healthy"
