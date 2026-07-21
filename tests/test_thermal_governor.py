import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from egregore.kernel.provenance import Provenance
from egregore.powertrain.gearbox import Gear, Gearbox, GearboxConfig
from egregore.powertrain.thermal_governor import ThermalGovernorTestMode, ThermalSample


def test_thermal_governor_emits_only_when_gear_is_g5(tmp_path):
    # Deterministic timing for ts_ns
    t = {"v": 1}

    def now_ns():
        t["v"] += 1
        return t["v"]

    # 32-byte Ed25519 signing key seed (hex). Any deterministic seed is fine for unit tests.
    signing_key_hex = "00" * 32

    zarc_path = tmp_path / "run.zarc"
    prov = Provenance(
        zarc_path,
        signing_key_hex=signing_key_hex,
        now_ns=now_ns,
        prev_hash_init="0" * 64,
    )

    cfg = GearboxConfig(q_high=100, q_block=500, g5_to_g2_cooldown_s=30.0)
    gb = Gearbox(config=cfg, initial=Gear.G0, now_s=lambda: 0.0)

    governor = ThermalGovernorTestMode(gearbox=gb, provenance=prov)

    samples = [
        ThermalSample(temp_c=50.0, vram_pct=10.0, depth=0, now_s=1.0),  # should stay G0
        ThermalSample(
            temp_c=83.0, vram_pct=10.0, depth=0, now_s=2.0
        ),  # shift to G5 by temp >= 83
        ThermalSample(
            temp_c=60.0, vram_pct=10.0, depth=0, now_s=3.0
        ),  # may stay G5 (no cooldown gating for upshift->down unless in G5)
        ThermalSample(
            temp_c=77.0, vram_pct=94.0, depth=0, now_s=4.0
        ),  # still triggers G5 if vram>=95 (here 94 so maybe not)
    ]

    emitted = governor.run(samples)
    # From policy:
    # - sample[1] triggers upshift to G5 at now_s=2.0
    # - sample[2] and sample[3] remain in G5 because cooldown (30s) is not satisfied yet
    assert emitted == 3

    assert zarc_path.exists()
    assert prov.verify_chain() is True

    entries = list(prov.iter_entries())
    assert len(entries) == 3
    assert all(e.engine == "thermal" and e.event == "PRESSURE_ENERGY" for e in entries)
