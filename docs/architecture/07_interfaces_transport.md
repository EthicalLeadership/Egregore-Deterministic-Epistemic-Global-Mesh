# 07 — Interfaces & Transport

Status: `implemented with defects`

The interface layer defines the contracts (ports) that separate Plane 1 from Plane 2. The HTTP API, mycelial network, and quarantine modules live at the atmosphere edge.

## Ports

Ports are Python Protocols declared in `src/egregore/interface/`. They allow the core to declare what it needs without depending on concrete infrastructure.

### Semantics ports

Source: `src/egregore/interface/semantics_ports.py`.

| Port | Purpose |
|---|---|
| `IAuthzProvider` | Authorize `generate_dossier` commands |
| `ICaseStore` | Read case state and next version number |
| `IIdempotencyStore` | Cache/lookup success results by fingerprint |
| `ISnapshotStore` / `IEventLogStore` / `IOutboxStore` / `IUsageCounterStore` | Granular persistence split (not used by current executor) |
| `ISemanticsDomainAdapter` | Name event/outbox types for artifact derivation |
| `ITransactionalPersistence` | Atomic T2 commit boundary |
| `IProvenanceSigner` | Sign canonical bytes for `.zarc` |
| `IKimik2Loader` | Deterministic inference loader port |

`CommitResult` is also defined here as a value object, which collides with `domain.models.dossier.CommitResult`.

### Constraint binding ports

Source: `src/egregore/interface/constraint_binding_ports.py`.

Defines the CBI-0 protocols and error types:

- `IProjectionAccessMonitor` (M1)
- `IProjectionRegistryValidator` (M2)
- `ICompositionGuard` (M3)
- `IBindingAuditEmitter` (M4)

Plus errors: `ProjectionBindingError`, `RegistryValidationError`, `CompositionGuardError`.

### Legal agent ports

Source: `src/egregore/interface/legal_agent_ports.py`.

- `IRuleRegistry` — source of legal rules.
- `ILegalAgent` — agent analyze boundary.
- `validate_legal_analysis_output()` — BIOK structural boundary check.

### Other ports

| Port | File | Purpose |
|---|---|---|
| `IProvenanceSink` / `IProvenanceVerifier` | `provenance_port.py` | Append/verify provenance events |
| `ICommitJournal` | `zarc_journal_ports.py` | Read committed snapshots/events/outbox |
| `IGearboxPolicy` | `gearbox_port.py` | Gearbox transition decision |
| `ITransitLayer` / `IHardwareProbe` / etc. | `hardware_ports.py` | Hardware/transport abstractions |
| `IAgentRouter` / `IResultCombiner` | `orchestration_ports.py` | Multi-agent routing/combining (open gap) |
| `DossierServiceFacade` | `ports/dossier_ports.py` | HTTP-to-application facade contract |

## Dossier service facade

Source: `src/egregore/interface/ports/dossier_ports.py`.

`DossierServiceFacade` is the contract that the HTTP layer uses to call the application layer:

- `generate(request: DossierGenerateRequest) -> DossierGenerateResult`

`DossierGenerateRequest` carries all fields needed by `CorePlaneGenerateDossierExecutor`, plus a transport-only `vertical` hint.

## HTTP API

Source: `src/egregore/http_api/http/`.

`create_app()` in `app.py` assembles a FastAPI application with routers. `main.py` is the uvicorn entrypoint; it catches import errors so the module remains import-safe when FastAPI is not installed. `v1/zarc_config.py` is a dev/demo module with a fixed Ed25519 signing key (not for production).

Routers:

- `/v1/dossiers/generate` — `v1/dossiers.py`
- `/v1/intake/upload` — `v1/intake.py`
- `/workflows/test-health`, `/workflows/{id}` — `v1/workflows.py`
- `/admin/invite`, `/signup` — `v1/auth.py`
- `/ws/chat/{session_id}` — `v1/ws_chat.py`

Static files are served under `/services`.

### Dossiers router

`v1/dossiers.py` validates transport fields, builds a `DossierGenerateRequest`, and delegates to `facade.generate()`. It uses `importlib` to fetch the facade dependency without static cross-layer imports.

Known defect: `get_service()` references `get_dossier_facade` without importing it, so the back-compat helper is currently broken.

### Intake router

`v1/intake.py` accepts multipart file uploads, extracts text via `application/document_intake`, builds a dossier request, and generates a dossier.

### Workflows router

`v1/workflows.py` provides a `/workflows/test-health` endpoint that uses deterministic hashing to build a health-check dossier request and tracks workflow state in an in-memory dict.

### Auth router

`v1/auth.py` provides invite/signup endpoints with in-memory repositories. Provenance logging is stubbed.

### WebSocket chat router

`v1/ws_chat.py` provides a `/ws/chat/{session_id}` endpoint that converts messages to deterministic dossier requests and streams results.

## Shared user models

Source: `src/egregore/models/user.py`.

`User`, `Invite`, and `Account` are frozen dataclasses used by the HTTP auth router. They include provenance fields intended to reference `.zarc` chain entries.

## Mycelial network

Source: `src/egregore/interface/mycelial_network.py`.

Status: `stub`.

`MycelialMessage` and `IMycelialNetwork` define inter-agency messaging:

- Message types: `ALERT`, `THEORY`, `GOSSIP`, `LABOR`, `ART`
- TTL hop decay
- Circular charge transfer
- `MycelialMesh` is a minimal in-memory implementation

This is conceptual scaffolding; it is not integrated into the dossier runtime.

## DNI-2 quarantine

Source: `src/egregore/interface/dni_2_quarantine.py`.

Status: `implemented with defects`.

The DNI-2 atmosphere border is intended to:

- Inspect ingress work units
- Check energy budgets
- Quarantine by threat score
- Filter egress by sensitivity and provenance chain length

Known defects:

- Imports `egregore.domain.work_unit` and `egregore.interface.ops.ops_ports`, neither of which exist.
- Uses `time.time()` for timestamps, violating core determinism rules.

This module is currently non-importable.

## Local transit layer

Source: `src/egregore/infrastructure/local_transit_layer.py`.

A thread-safe `ITransitLayer` implementation using `queue.Queue`. Used by the turbine/gearbox runtime.

## Invariants

| Invariant | Enforcement |
|---|---|
| Interface may only import domain | `tests/test_arch_enforcement.py` allows `domain` and `shared`. |
| HTTP API may import application/domain/models | Allowed by layer matrix. |
| No direct Plane 1 → Plane 2 imports | AST tests. |
| Ports are Protocols | All interface contracts use `typing.Protocol`. |

## Known defects

- `dni_2_quarantine.py` has broken imports and wall-clock timestamps.
- `dossiers.py` back-compat `get_service()` references an undefined import.
- `auth.py` provenance logging is stubbed and uses wall-clock time.

## Tests

- `tests/interfaces/http/test_dossiers.py`
- `tests/interfaces/http/test_intake.py`
- `tests/interfaces/http/test_workflows.py`
- `tests/test_architecture_policy_intent.py`
- `tests/test_arch_enforcement.py`
