# 08 — Powertrain & DT1

Status: `implemented & tested`

The powertrain and DT1 subsystems handle deterministic throughput regulation, thermal-aware gear selection, and distributed work scheduling. They live in Plane 2 (projection/runtime) but interact with the domain policy layer.

## Powertrain / Gearbox

### Domain policy

Source: `src/egregore/domain/gearbox_policy.py`.

`GearboxPolicy.decide(...)` is a pure, stateless function that maps `{state, temp_c, vram_pct, depth, now_s}` to a `GearboxTransition`. Gears:

- `G0` — idle / low load
- `G2` — normal throughput
- `G5` — emergency / throttled

Rules:

- Emergency upshift to G5 if `temp_c >= 83`, `vram_pct >= 95`, or `depth >= q_block`.
- Hysteresis keeps G5 until `temp_c < 78`, `depth < q_high`, and cooldown elapsed.
- Downshift to G0 when depth is zero and temp is low.

### State & config

Source: `src/egregore/domain/gearbox_state.py`, `src/egregore/domain/gearbox_config.py`.

- `Gear` enum: `G0=0`, `G2=2`, `G5=5`.
- `GearboxState(gear, last_shift_s)` — immutable state.
- `GearboxConfig(q_high, q_block, g5_to_g2_cooldown_s)` — thresholds.

### Powertrain adapter

Source: `src/egregore/powertrain/gearbox.py`.

`Gearbox` is a stateful wrapper around the pure domain policy:

- Maintains current gear and `last_shift_s`.
- `evaluate(temp_c, vram_pct, depth, now_s)` delegates to `GearboxPolicy.decide()` and updates internal state.
- Exposes `domain_state()` for application adapters.

### Thermal types

Source: `src/egregore/interface/thermal_types.py`.

`ThermalSample` is the simple frozen dataclass used by the thermal governor: `temp_c`, `vram_pct`, `depth`, `now_s`.

### Thermal governor

Source: `src/egregore/powertrain/thermal_governor.py`.

`ThermalGovernorTestMode` is a CPU-only deterministic wrapper that:

- Accepts a `Gearbox` and a `Provenance` writer.
- Processes a sequence of `ThermalSample`s through `ThermalGovernorService` (application layer).
- Emits `.zarc` events via `ZarcProvenanceSink` when G5 is entered.

This keeps side effects behind the `IProvenanceSink` port.

### Application service

Source: `src/egregore/application/thermal_governor_service.py`, `src/egregore/application/gearbox_evaluate_policy_adapter.py`.

`ThermalGovernorService` orchestrates thermal samples and emits provenance events. `GearboxEvaluatePolicyAdapter` adapts the stateful `Gearbox` to a policy interface expected by the service.

## DT1 / Panelmesh runtime

DT1 is the deterministic distributed-runtime subsystem. It schedules `WorkUnit`s as `Panel`s on nodes under credit-lease, pressure, and admission constraints.

### Models

Source: `src/egregore/dt1/models.py`.

Key protobuf-aligned types:

- `WorkUnit` — the unit of work; contains `WorkUnitEnvelope` + `SpanRef`s.
- `WorkUnitResult` — terminal result.
- `PressureSignal` — resource-pressure telemetry.
- `LaneKey` — scheduling lane identity `(dt1_class, dt1_type, priority, site)`.
- `CreditGrant` / `CreditRevoke` — credit lease updates.
- `BladeDispatchOutcome` — admission decision (`ACCEPTED`, `DEFERRED`, `REJECTED`, `PUBLISHED`).

Enums:

- `Dt1Class`, `Priority`, `RoutingHint`, `WorkUnitStatus`, `PressureReason`, `WorkSpanKind`.

### Panelmesh phase-1 runtime

Source: `src/egregore/dt1/panelmesh_phase1_runtime.py`.

`Panel` and `NodeCapability` define the scheduling problem. `Phase1State` tracks:

- `tick`
- panel lifecycle records (`QUEUED → LEASED → RUNNING → SUCCEEDED/FAILED/TIMED_OUT → ARCHIVED`)
- job-to-panel mapping
- dedupe success cache
- round-robin scheduling index

`step_phase1(state, panels, inputs)` is the deterministic single-tick scheduler:

1. Reclaim expired leases.
2. Dispatch queued panels to eligible nodes (RR + affinity).
3. Advance `LEASED → RUNNING`, invoke injected `RunnerCallable`, handle timeouts/failures, dedupe successes.
4. Archive terminal panels.

Properties:

- No I/O; runner is injected.
- Deterministic given state + inputs.
- Lease TTL expressed in ticks.
- Retry with backoff.

### Panelmesh phase-1 orchestrator

Source: `src/egregore/dt1/panelmesh_phase1_orchestrator.py`.

`PanelmeshPhase1Orchestrator` ties together:

- Credit lease state machine
- Pressure aggregation + hysteresis
- ES admission decision
- Panelmesh phase-1 scheduling tick

One `tick(...)` performs:

1. Apply credit lease updates per lane.
2. Aggregate pressure signals and apply hysteresis.
3. Run ES admission for pending work units.
4. Convert admitted work units to panels.
5. Run one `step_phase1` tick.

### State machines

Source: `src/egregore/dt1/state_machines/`.

| Module | Purpose |
|---|---|
| `credit_lease_sm.py` | Deterministic credit lease transitions (`NO_CREDITS → ACTIVE → STALE`) |
| `es_admission_sm.py` | Edge admission/publish decision under pressure and credits |
| `pressure_aggregation_sm.py` | Aggregate pressure signals + hysteresis debounce |

### Supporting DT1 modules

| Module | Purpose |
|---|---|
| `runtime_controller.py` | Runtime controller |
| `inference_work_unit.py` | Inference work unit mapping |
| `deterministic_runner_adapter.py` | Deterministic runner adapter |
| `subjects.py` | Subject taxonomy |
| `dt1_field_compute_comparative_report.py` | Comparative report generation |

## Invariants

| Invariant | Enforcement |
|---|---|
| Gearbox policy is pure | `GearboxPolicy` has no mutable state. |
| Thermal governor uses ports | Side effects via `IProvenanceSink`. |
| Panelmesh is tick-based and deterministic | `step_phase1` returns new state + events. |
| Runner is injected | No direct model invocation in runtime. |
| DT1 models are frozen dataclasses | Immutable work units and signals. |

## Tests

- `tests/test_gearbox.py`
- `tests/test_thermal_governor.py`
- `tests/test_panelmesh_phase1_runtime.py`
- `tests/test_panelmesh_phase1_orchestrator.py`
- `tests/test_dt1_credit_lease_sm.py`
- `tests/test_dt1_pressure_aggregation_sm.py`
- `tests/test_dt1_subjects.py`
- `tests/test_dt1_kimik2_integration.py`
