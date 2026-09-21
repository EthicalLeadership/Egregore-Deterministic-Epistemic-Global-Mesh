# 10 — Testing & Architecture Governance

Egregore uses pytest plus AST-based architecture tests to enforce design rules. The architecture tests are not optional style checks; they protect the two-plane separation and single-source-of-truth invariants.

## Test suite structure

```
tests/
├── conftest.py
├── test_architecture_policy_intent.py      # Single-source + policy tests
├── test_arch_enforcement.py                # Layer dependency + purity tests
├── test_cbi_0_*.py                         # CBI-0 governance tests
├── test_semantics_*.py                     # Executor / replay / envelope tests
├── test_canonical_ir.py                    # Canonical IR contract
├── test_reasoning_guard.py                 # Evidence-to-conclusion boundary
├── test_legal_agent_*.py                   # Legal agent pipeline
├── test_gearbox.py                         # Gearbox transitions
├── test_panelmesh_*.py                     # DT1 panelmesh
├── test_dt1_*.py                           # DT1 state machines
├── test_local_*.py                         # Local LLM/catalog
├── test_nats_broker.py                     # JetStream bootstrap
├── test_pulse_adapter.py                   # Telemetry pulse
├── test_sqlite_dossier_adapter.py          # SQLite persistence
├── test_external_ledger_to_zarc.py         # Ledger bridge
├── test_anchorum_ingest_flow.py            # ANCHORUM ingest
├── interfaces/http/test_*.py               # HTTP router tests
└── ...
```

## Architecture policy tests

Source: `tests/test_architecture_policy_intent.py`.

| Test | Rule |
|---|---|
| `test_cross_layer_imports_are_explicitly_allowed` | Every layer may import only the internal packages listed in `ALLOWED_CROSS_LAYER`. |
| `test_audit_event_construction_is_single_sourced` | `AuditEvent(...)` may only be constructed in `domain/semantics/derivations.py`. |
| `test_outbox_entry_construction_is_single_sourced` | `OutboxEntry(...)` may only be constructed in `domain/semantics/derivations.py`. |
| `test_commit_generate_t2_call_is_executor_only` | `commit_generate_t2()` may only be called from `application/semantics_executor.py`. |
| `test_domain_remains_pure_of_application_and_infrastructure` | `domain/` must not import `application/` or `infrastructure/`. |
| `test_json_loads_and_dumps_have_one_home` | `json.loads`/`json.dumps` may only appear in `shared/canonical.py`. |
| `test_type_ignores_have_known_justification` | `type: ignore` must have a justification or be allowlisted. |

## Architecture enforcement tests

Source: `tests/test_arch_enforcement.py`.

| Test | Rule |
|---|---|
| `test_dependency_rules_enforced` | `domain/` and `application/` must not import forbidden prefixes (`infrastructure/`, `application/` for domain). |
| `test_domain_purity_no_filesystem_network_heuristics` | `domain/` must not import `pathlib`, `os`, `sys`, `socket`, `requests`, `urllib`, `http`, `multiprocessing`, or call `open()`. |
| `test_canonicalization_single_source_of_truth` | `canonical_json` and `sha256_hex` must be defined only in `shared/canonical.py`. |
| `test_legal_reasoning_engine_import_surface_is_closed` | Only `application/cbi_0_orchestrated_executor.py` may import `LegalReasoningEngine`. |
| `test_execution_authority_usage_surface_is_closed` | Only `application/cbi_0_orchestrated_executor.py`, `application/legal_reasoning_engine.py`, and `domain/legal_agent/execution_authority.py` may reference `ExecutionAuthority`. |
| `test_layer_dependency_matrix_is_stable` | Every top-level package layer must appear in `ALLOWED_LAYER_DEPENDENCIES`, and all imports must respect the matrix. |

## Allowed layer dependency matrix

| Layer | Allowed internal dependencies |
|---|---|
| `application` | `domain`, `http_api`, `interface`, `kernel`, `models`, `powertrain`, `shared` |
| `bus` | *(none)* |
| `cortex` | `shared` |
| `domain` | `interface`, `shared` |
| `dt1` | *(none)* |
| `governance` | `shared` |
| `infrastructure` | `domain`, `interface`, `kernel` |
| `interface` | `domain` |
| `http_api` | `application`, `domain`, `models` |
| `kernel` | `shared` |
| `models` | `shared` |
| `powertrain` | `application`, `domain`, `infrastructure`, `kernel` |
| `shared` | `domain` |

## Current test status

As of the latest run:

```bash
pytest -q --ignore=tests/infrastructure/test_postgresql_persistence_mocked.py
```

- Two tests fail:
  - `test_json_loads_and_dumps_have_one_home` — triggered by `infrastructure/adapters/postgresql_persistence.py` using `json.loads`/`json.dumps`.
  - `test_canon_yaml_schema` — requires an external file `extracted_from_usb/control_phases/myth/canon.yaml` that is not present.
- One test file is broken:
  - `tests/infrastructure/test_postgresql_persistence_mocked.py` — syntax error at line 8.

Excluding the broken file, the suite passes at roughly 92%.

See [11 — Repository State](11_repository_state.md) for the full status.

## Why AST tests instead of runtime tests?

Architecture rules are structural: which module imports which other module, which file constructs a datatype, which file defines a function. Runtime tests cannot reliably catch these because the violations are visible only in source code. AST tests scan every `.py` file and fail the build if the rules drift.

## Adding a new layer or port

If you add a new top-level package under `src/egregore/`:

1. Add the layer to `ALLOWED_LAYER_DEPENDENCIES` in both architecture tests.
2. Declare its allowed internal dependencies explicitly.
3. Re-run `pytest tests/test_arch_enforcement.py tests/test_architecture_policy_intent.py`.

If you add a new single-source type (e.g., a new audit artifact):

1. Add a test analogous to `test_audit_event_construction_is_single_sourced`.
2. Add an allowlist exception if the constructor must be called from one specific module.

## Running the tests

```bash
# Full suite (currently blocked by syntax error)
pytest -q

# Excluding broken file
pytest -q --ignore=tests/infrastructure/test_postgresql_persistence_mocked.py

# Architecture tests only
pytest tests/test_arch_enforcement.py tests/test_architecture_policy_intent.py -q

# Specific subsystem
pytest tests/test_gearbox.py tests/test_panelmesh_phase1_runtime.py -q
```
