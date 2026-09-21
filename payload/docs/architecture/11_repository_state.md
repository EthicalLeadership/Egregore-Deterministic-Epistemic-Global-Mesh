# 11 — Repository State

This document is a snapshot of the repository as of the documentation pass. It includes tracked vs. untracked files, known defects, failing tests, stubs, and gaps.

## Test status

Latest run (excluding the broken PostgreSQL test file):

```bash
pytest -q --ignore=tests/infrastructure/test_postgresql_persistence_mocked.py
```

Result: **2 failures** out of the remaining suite.

| Failure | Cause |
|---|---|
| `tests/test_architecture_policy_intent.py::test_json_loads_and_dumps_have_one_home` | `src/egregore/infrastructure/adapters/postgresql_persistence.py` calls `json.loads`/`json.dumps` directly. |
| `tests/test_canon_yaml_schema.py::test_canon_yaml_schema` | Missing external file `extracted_from_usb/control_phases/myth/canon.yaml`. |

The rest of the suite passes (≈92% pass rate when the broken file is excluded).

The `.update_log` contains 54 consecutive `UPDATE_BLOCKED: tests failed` entries, driven primarily by the broken PostgreSQL test file and the two failures above.

## Tracked vs. untracked

`git status --short` shows:

**Modified tracked files:**

- `.update_log`
- `src/egregore/infrastructure/persistence/__init__.py`

**Untracked files/directories:**

- `atgca/` — C++ ATGCA subproject
- `docs/` — this documentation set
- `src/egregore/domain/__init__.py`
- `src/egregore/domain/agency_taxonomy.py`
- `src/egregore/domain/models/`
- `src/egregore/infrastructure/adapters/postgresql_persistence.py`
- `src/egregore/infrastructure/sediment_archive.py`
- `src/egregore/interface/dni_2_quarantine.py`
- `src/egregore/interface/mycelial_network.py`

These untracked additions are mid-reorganization: they have not been committed and some are incomplete or defective.

## Implemented & tested

| Subsystem | Status | Notes |
|---|---|---|
| `.zarc` provenance | ✅ | `kernel/provenance.py` |
| Canonical JSON / hashing | ✅ | `shared/canonical.py` |
| Canonical semantic IR | ✅ | `domain/semantics/canonical_ir.py` |
| CBI-0 M1–M4 | ✅ | `application/cbi_0_*.py` |
| Legal reasoning engine | ✅ | `application/legal_reasoning_engine.py` |
| Execution authority | ✅ | `domain/legal_agent/execution_authority.py` |
| Zarc journal | ✅ | `infrastructure/zarc_journal.py` |
| SQLite persistence | ✅ | `infrastructure/persistence/sqlite_dossier_adapter.py` |
| ANCHORUM bridge/comparator | ✅ | `governance/anchorum_*.py` |
| Gearbox / thermal governor | ✅ | `powertrain/`, `domain/gearbox_*.py` |
| Panelmesh phase-1 runtime | ✅ | `dt1/panelmesh_phase1_runtime.py` |
| Panelmesh orchestrator | ✅ | `dt1/panelmesh_phase1_orchestrator.py` |
| DT1 state machines | ✅ | `dt1/state_machines/*.py` |
| Local model catalog | ✅ | `infrastructure/local_model_catalog.py` |
| Telemetry / pulse | ✅ | `infrastructure/telemetry/`, `cortex/pulse_adapter.py` |
| NATS broker bootstrap | ✅ | `bus/nats_broker.py` |

## Implemented with defects

| Subsystem | Defect |
|---|---|
| PostgreSQL persistence | Duplicate `query_audit_log`; conflicting `CommitResult` imports; direct `json.loads`/`json.dumps`; wrong `commit_generate_t2` signature. |
| HTTP dossiers router | `get_service()` references `get_dossier_facade` without importing it. |
| DNI-2 quarantine | Imports non-existent modules `egregore.domain.work_unit` and `egregore.interface.ops.ops_ports`; uses `time.time()`. |
| Sediment archive | Uses `datetime.utcnow()` and `time.time()` for timestamps/epochs. |
| Auth router | Uses wall-clock time and stubs provenance logging. |
| Canon YAML schema test | Requires missing external path. |

## Stubs / placeholders

| File | Status |
|---|---|
| `src/egregore/dag.py` | Stub |
| `src/egregore/dashboard.py` | Stub |
| `src/egregore/launch_control.py` | Stub |
| `src/egregore/meta_governor.py` | Stub |
| `src/egregore/registry.py` | Stub |
| `src/egregore/interface/mycelial_network.py` | Conceptual stub; minimal implementation |
| `src/egregore/version.py` | Single-line version constant (`0.0.0`) |

## Missing / not yet implemented

| Subsystem | Where documented | Status |
|---|---|---|
| Mantle / ops layer | `01_geological_model.md` | `domain/ops/` and `infrastructure/ops/` do not exist. `IEnergyGovernor` referenced by DNI-2 is missing. |
| Multi-agent composition law | `interface/orchestration_ports.py` | Transition algebra and formal projection equivalence are TODOs. |
| Chaos engineering | `01_geological_model.md` | Referenced but not implemented. |
| Real telemetry sensors | `06_infrastructure_adapters.md` | CPU/GPU metrics are best-effort; many values degrade to zero. |
| Production key management | `02_zarc_provenance.md` | Default test signing key `"01" * 32` is hard-coded in tests/adapters. |
| HTTP auth implementation | `07_interfaces_transport.md` | In-memory repositories; no real identity provider. |
| PostgreSQL schema | `06_infrastructure_adapters.md` | Schema file `schema.sql` referenced but not present in repo. |

## ATGCA C++ subproject

Source: `atgca/`.

A CMake-based C++20 static library + GoogleTest executable:

- `GearboxCore`
- `HysteresisController`
- `StateMachine`
- `TorqueAllocator`
- `TurbineBase`
- `ConfigLoader`

Tests cover conservation (T07), hysteresis (T08), and determinism (T09). It is untracked and not integrated into the Python test suite.

## Recommended next steps

1. Fix `tests/infrastructure/test_postgresql_persistence_mocked.py` syntax error to unblock pytest collection.
2. Repair PostgreSQL adapter:
   - Remove duplicate `query_audit_log`.
   - Consolidate `CommitResult` import.
   - Replace `json.loads`/`json.dumps` with `shared/canonical.py`.
   - Align `commit_generate_t2` signature with `ITransactionalPersistence`.
3. Fix `dni_2_quarantine.py` imports or remove the module until ops layer exists.
4. Fix `http_api/http/v1/dossiers.py` missing `get_dossier_facade` import.
5. Decide whether to commit or remove untracked modules.
6. Provide `canon.yaml` or mark `test_canon_yaml_schema` as requiring external fixtures.
7. Run full suite green, then clear `.update_log`.
