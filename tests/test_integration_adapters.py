import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from egregore.bus.nats_broker import StreamBootstrapConfig, bootstrap_jetstream
from egregore.cortex.pulse_adapter import PulseAdapter
from egregore.governance.anchorum_bridge import AnchorumBridge
from egregore.kernel.dfih_bridge import zarc_lines_to_execution_traces
from egregore.kernel.provenance import Provenance
from egregore.powertrain.gearbox import Gear, Gearbox, GearboxConfig
from egregore.powertrain.thermal_governor import ThermalGovernorTestMode, ThermalSample


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, payload: bytes) -> Any:
        self.published.append((subject, payload))
        return {"ok": True}


class FakeJetStream:
    def __init__(self, *, existing_stream_names: set[str] | None = None) -> None:
        self.existing = existing_stream_names or set()
        self.added: list[dict] = []

    async def add_stream(self, config: dict) -> None:
        name = config["name"]
        if name in self.existing:
            raise Exception("Already exists: stream already exists")
        self.added.append(config)


@pytest.mark.asyncio
async def test_end_to_end_adapter_flow_cpu_only(tmp_path: Path) -> None:
    # ---------- provenance + thermal governor ----------
    zarc_path = tmp_path / "trace.zarc"
    t = {"v": 0}

    def now_ns() -> int:
        t["v"] += 1
        return 1000 + t["v"]

    signing_key_hex = "01" * 32
    prov = Provenance(
        zarc_path,
        signing_key_hex=signing_key_hex,
        now_ns=now_ns,
        prev_hash_init="00" * 32,
    )

    cfg = GearboxConfig(q_high=100, q_block=500, g5_to_g2_cooldown_s=9999.0)
    gb = Gearbox(config=cfg, initial=Gear.G0, now_s=lambda: 0.0)
    governor = ThermalGovernorTestMode(gearbox=gb, provenance=prov)

    # Pick samples that force G5 via temp>=83; with huge cooldown we keep G5.
    samples = [
        ThermalSample(temp_c=83.0, vram_pct=10.0, depth=0, now_s=1.0),
        ThermalSample(temp_c=90.0, vram_pct=10.0, depth=0, now_s=2.0),
        ThermalSample(temp_c=84.0, vram_pct=10.0, depth=0, now_s=3.0),
    ]
    emitted = governor.run(samples)
    assert emitted == 3
    assert prov.verify_chain() is True

    # ---------- zarc -> dfih bridge ----------
    traces = list(
        zarc_lines_to_execution_traces(
            zarc_path.read_text(encoding="utf-8").splitlines()
        )
    )
    assert len(traces) == 3
    assert all(t.stage.name == "PRESSURE_ENERGY" for t in traces)
    # engine == "thermal" => fault_injection active with reason None
    # Bridge is now format-only: fault_injection.active is True iff payload.reason is present.
    assert all(t.stage.fault_injection is None for t in traces)

    # ---------- dfih indirectly validated via anchorum bridge ingest ----------
    ingested: list[dict[str, Any]] = []

    def vault_ingest(batch: list[Mapping[str, Any]]) -> Any:  # type: ignore[name-defined]
        # Keep it deterministic for assertions.
        ingested.extend(list(batch))
        return {"ingested": len(batch)}

    anchorum = AnchorumBridge(zarc_path=zarc_path, vault_ingest=vault_ingest)
    result = anchorum.sync(last_n=2)
    assert isinstance(result, dict)
    assert result["ingested"] == 2
    assert len(ingested) == 2
    assert all(r["content_type"] == "application/x-egregore-zarc" for r in ingested)
    assert all(isinstance(r["raw_bytes"], (bytes, bytearray)) for r in ingested)
    assert all("sig_hex" in r["metadata"] for r in ingested)

    # ---------- pulse adapter ----------
    publisher = FakePublisher()

    pulse = PulseAdapter(
        publisher=publisher,
        node_id="pioneer1",
        get_ts_ns=lambda: 123,
        get_cpu_pct=lambda: 1.5,
        get_gpu_temp_c=lambda: 70.0,
        get_gpu_power_mw=lambda: 2500,
        get_vram_used=lambda: 100,
        get_vram_total=lambda: 200,
    )
    payload = await pulse.emit()
    assert publisher.published == [("obs.pulse.pioneer1", payload)]

    import json

    obj = json.loads(payload.decode("utf-8"))
    assert obj["node_id"] == "pioneer1"
    assert obj["gpu_power_mw"] == 2500

    # ---------- nats broker bootstrap ----------
    js = FakeJetStream(existing_stream_names={"DT1_LIVE"})
    stream_configs = [
        StreamBootstrapConfig(
            name="DT1_LIVE",
            subjects=["dt1.*.*.*.*"],
            retention="limits",
            storage="file",
            max_age_seconds=1800,
        ),
        StreamBootstrapConfig(
            name="CTRL",
            subjects=["ctrl.>"],
            retention="limits",
            storage="memory",
            max_age_seconds=3600,
        ),
    ]
    await bootstrap_jetstream(js, stream_configs=stream_configs)
    assert [c["name"] for c in js.added] == ["CTRL"]
