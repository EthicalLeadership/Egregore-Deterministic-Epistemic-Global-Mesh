import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from egregore.cortex.pulse_adapter import (
    PulseAdapter,
    PulseSample,
    build_pulse_payload,
)


def test_build_pulse_payload_is_deterministic():
    sample = PulseSample(
        ts_ns=123,
        node_id="node-1",
        cpu_pct=1.5,
        gpu_temp_c=70.0,
        gpu_power_mw=2500,
        vram_used=100,
        vram_total=200,
    )
    payload = build_pulse_payload(sample)

    # Stable JSON encoding; payload should be valid JSON and include expected keys.
    import json

    obj = json.loads(payload.decode("utf-8"))
    assert obj["ts"] == 123
    assert obj["node_id"] == "node-1"
    assert obj["cpu_pct"] == 1.5
    assert obj["gpu_temp"] == 70.0
    assert obj["gpu_power_mw"] == 2500
    assert obj["vram_used"] == 100
    assert obj["vram_total"] == 200


@pytest.mark.asyncio
async def test_pulse_adapter_emits_to_obs_pulse_subject():
    published: list[tuple[str, bytes]] = []

    class FakePublisher:
        async def publish(self, subject: str, payload: bytes) -> Any:
            published.append((subject, payload))
            return {"ok": True}

    publisher = FakePublisher()

    t = {"ts": 999}
    cpu = {"v": 10.0}
    temp = {"v": 80.0}
    power = {"v": 1234}
    used = {"v": 555}
    total = {"v": 1000}

    adapter = PulseAdapter(
        publisher=publisher,
        node_id="pioneer1",
        get_ts_ns=lambda: t["ts"],
        get_cpu_pct=lambda: cpu["v"],
        get_gpu_temp_c=lambda: temp["v"],
        get_gpu_power_mw=lambda: power["v"],
        get_vram_used=lambda: used["v"],
        get_vram_total=lambda: total["v"],
    )

    payload = await adapter.emit()
    assert len(published) == 1
    subject, sent = published[0]
    assert subject == "obs.pulse.pioneer1"
    assert sent == payload
