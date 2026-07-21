# ATGCA — Adaptive Turbine–Gearbox Compute Architecture

Phase 1 Foundation implementation per the ATGCA Swarm Orchestration Master.

## Build

```bash
cd atgca
make
make test
```

## Test suites

- `T07 Conservation` — 1000 frames, verifies `Σ(Allocated) ≤ TotalTorque × GearRatio` within 1%.
- `T08 Hysteresis` — verifies state transitions respect sacred frame delays and Protection is immediate.
- `T09 Determinism` — identical seed/config produces bit-for-bit identical telemetry.

## Status

Phase 1: Foundation complete.
