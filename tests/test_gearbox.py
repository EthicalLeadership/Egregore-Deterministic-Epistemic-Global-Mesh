import sys
from pathlib import Path

# Ensure `src/` is importable when running `pytest` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from egregore.powertrain.gearbox import Gear, Gearbox, GearboxConfig


def test_emergency_upshift_to_g5_by_temp():
    gb = Gearbox(config=GearboxConfig(q_block=500))
    assert gb.gear == Gear.G0
    g = gb.evaluate(temp_c=83.0, vram_pct=10.0, depth=0, now_s=1.0)
    assert g == Gear.G5
    assert gb.gear == Gear.G5


def test_emergency_upshift_to_g5_by_vram():
    gb = Gearbox(config=GearboxConfig(q_block=500))
    g = gb.evaluate(temp_c=50.0, vram_pct=95.0, depth=0, now_s=1.0)
    assert g == Gear.G5


def test_emergency_upshift_to_g5_by_depth():
    gb = Gearbox(config=GearboxConfig(q_block=500))
    g = gb.evaluate(temp_c=50.0, vram_pct=10.0, depth=500, now_s=1.0)
    assert g == Gear.G5


def test_g5_to_g2_shift_only_after_cooldown():
    cfg = GearboxConfig(q_high=100, q_block=500, g5_to_g2_cooldown_s=10.0)
    gb = Gearbox(config=cfg, initial=Gear.G5, now_s=lambda: 0.0)

    # Preconditions for shift down:
    # - t < 78.0
    # - depth < q_high
    # - cooldown not satisfied yet (now - last_shift_s <= 10)
    g1 = gb.evaluate(temp_c=77.9, vram_pct=10.0, depth=99, now_s=5.0)
    assert g1 == Gear.G5

    # Cooldown satisfied
    g2 = gb.evaluate(temp_c=77.9, vram_pct=10.0, depth=99, now_s=11.0)
    assert g2 == Gear.G2
    assert gb.gear == Gear.G2


def test_g5_does_not_shift_down_if_depth_not_low_enough():
    cfg = GearboxConfig(q_high=100, q_block=500, g5_to_g2_cooldown_s=1.0)
    gb = Gearbox(config=cfg, initial=Gear.G5, now_s=lambda: 0.0)

    # depth == q_high => not (depth < q_high)
    g = gb.evaluate(temp_c=77.0, vram_pct=10.0, depth=100, now_s=2.0)
    assert g == Gear.G5


def test_g5_does_not_shift_down_if_temp_not_cold_enough():
    cfg = GearboxConfig(q_high=100, q_block=500, g5_to_g2_cooldown_s=1.0)
    gb = Gearbox(config=cfg, initial=Gear.G5, now_s=lambda: 0.0)

    # temp >= 78 => no shift down
    g = gb.evaluate(temp_c=78.0, vram_pct=10.0, depth=10, now_s=2.0)
    assert g == Gear.G5


def test_shift_to_g2_when_depth_exceeds_q_high():
    cfg = GearboxConfig(q_high=100, q_block=500)
    gb = Gearbox(config=cfg, initial=Gear.G0, now_s=lambda: 0.0)

    g = gb.evaluate(temp_c=30.0, vram_pct=10.0, depth=101, now_s=1.0)
    assert g == Gear.G2


def test_shift_to_g0_when_idle_and_cold():
    cfg = GearboxConfig(q_high=100, q_block=500)
    gb = Gearbox(config=cfg, initial=Gear.G2, now_s=lambda: 0.0)

    g = gb.evaluate(temp_c=39.9, vram_pct=10.0, depth=0, now_s=1.0)
    assert g == Gear.G0


def test_no_shift_when_conditions_not_met():
    cfg = GearboxConfig(q_high=100, q_block=500)
    gb = Gearbox(config=cfg, initial=Gear.G0, now_s=lambda: 0.0)

    # depth not > q_high, depth not == 0 (so can't shift to G0 by idle rule)
    g = gb.evaluate(temp_c=50.0, vram_pct=10.0, depth=10, now_s=1.0)
    assert g == Gear.G0
    assert gb.gear == Gear.G0
