# 09 — Data Flows

This document traces end-to-end flows through Egregore with Mermaid diagrams.

## Flow 1: Dossier generation (happy path)

```mermaid
sequenceDiagram
    participant HTTP as HTTP API
    participant Facade as DossierServiceFacade
    participant EX as CorePlaneGenerateDossierExecutor
    participant AuthZ as IAuthzProvider
    participant CS as ICaseStore
    participant Compute as compute_engine_policy
    participant IR as Canonical IR boundary
    participant CBI as CBI-0 M1-M4
    participant Derive as derive_generate_artifacts
    participant PI as PI_O / EQUIV_O
    participant ZJ as ZarcJournal

    HTTP->>Facade: DossierGenerateRequest
    Facade->>EX: handle_generate_dossier(command, timestamp_ns)
    EX->>EX: derive_execution_id(command)
    EX->>EX: idempotency lookup
    EX->>AuthZ: authorize_generate(command)
    EX->>CS: get_case_state / get_next_version_number
    EX->>Compute: compute_engine_policy(command)
    Compute-->>EX: engine_out (data, metadata)
    EX->>IR: deserialize_to_canonical_ir(engine_out.data)
    IR-->>EX: CanonicalSemanticIR
    EX->>CBI: enforce_cbi0_runtime_chain_for_legal_ir(ir)
    CBI-->>EX: m4_record
    EX->>Derive: derive_generate_artifacts(...)
    Derive-->>EX: snapshot, events, outbox
    EX->>PI: project_from_artifacts (normal + reordered)
    EX->>PI: equivalent(candidate, reordered)
    EX->>ZJ: commit_generate_t2(...)
    ZJ-->>EX: CommandAck
    EX-->>Facade: CommandAck
    Facade-->>HTTP: {status: ok, data: result}
```

### Durable artifacts produced

- One `.zarc` entry (`engine="egregore_journal"`, `event="commit_generate_t2"`) containing:
  - command identity
  - version metadata
  - snapshot data
  - audit events
  - outbox entries
  - usage deltas
- In-memory read models updated deterministically.

## Flow 2: ANCHORUM offline ingest

```mermaid
flowchart LR
    A[.zarc file] --> B[AnchorumBridge]
    B --> C[OfflineVaultIngestCollector]
    C --> D[IngestRunReport]
    D --> E[Markdown / JSON report]
    D --> F[compare_anchorum_ingests]
```

1. `run_anchorum_bridge_ingest()` reads the last `N` `.zarc` entries.
2. `AnchorumBridge` packages each entry as a `VaultIngestRecord`.
3. `OfflineVaultIngestCollector` captures the batch in-memory.
4. An `IngestRunReport` is written to `--outdir`.
5. Two reports can be compared with `compare_anchorum_ingests()`.

Note: the governance-layer runner does **not** verify the `.zarc` chain because it must not import `kernel/`.

## Flow 3: Replay verification

```mermaid
sequenceDiagram
    participant ZJ as ZarcJournal
    participant Replay as CorePlaneReplayInterpreter
    participant Compute as compute_engine_policy
    participant IR as IR deserializer
    participant CBI as CBI-0
    participant PI as PI_O / EQUIV_O

    ZJ->>Replay: committed snapshot + events + outbox
    Replay->>Compute: recompute(command)
    Replay->>IR: deserialize with pinned reasoning_version_id
    Replay->>CBI: enforce_cbi0_runtime_chain_for_legal_ir
    Replay->>Replay: validate event/outbox identities
    Replay->>PI: project_from_artifacts(committed)
    Replay->>PI: project_from_artifacts(replayed)
    Replay->>PI: equivalent(...)
    Replay-->>ZJ: ReplayEquivalenceResult
```

Replay proves that the committed artifacts are reproducible from the original command plus the pinned versions.

## Flow 4: CBI-0 governance checkpoint

```mermaid
flowchart TD
    A[CanonicalSemanticIR] --> B[M2: Registry validator]
    B --> C[M1: Projection access monitor]
    C --> D[LegalReasoningEngine.analyze]
    D --> E[M3: Composition guard]
    E --> F[M4: Binding audit emitter]
    F --> G[BindingAuditRecord]
```

- M2 ensures descriptors and overlap classifications are complete/consistent.
- M1 ensures accessed IR fields are within declared scope.
- Legal reasoning runs only inside `ExecutionAuthority.governed()`.
- M3 blocks terminal output from re-entering canonical IR without a bridge.
- M4 emits a spec/runtime equivalence record.

## Flow 5: Thermal governor event path

```mermaid
sequenceDiagram
    participant TG as ThermalGovernorTestMode
    participant Svc as ThermalGovernorService
    participant Gear as Gearbox
    participant Sink as ZarcProvenanceSink
    participant Z as .zarc

    TG->>Svc: process(samples)
    loop each sample
        Svc->>Gear: evaluate(temp, vram, depth, now)
        Gear-->>Svc: gear
        alt gear == G5
            Svc->>Sink: append(ProvenanceEvent)
            Sink->>Z: Provenance.append(...)
        end
    end
```

Only G5 transitions are durably logged. The service uses the `IProvenanceSink` port, so the provenance backend can be swapped.

## Flow 6: DT1 panelmesh scheduling

```mermaid
flowchart TD
    A[Pending WorkUnits] --> B[Credit lease update]
    B --> C[Pressure aggregation]
    C --> D[ES admission decision]
    D -->|ACCEPTED/PUBLISHED| E[Convert to Panels]
    D -->|DEFERRED| F[Stay pending]
    D -->|REJECTED| G[Terminal workunits]
    E --> H[step_phase1]
    H --> I[LEASED]
    I --> J[RUNNING]
    J --> K[RunnerCallable]
    K -->|success| L[SUCCEEDED + dedupe cache]
    K -->|failure| M[Retry or FAILED]
    J -->|timeout| N[TIMED_OUT]
    L --> O[ARCHIVED]
    M --> O
    N --> O
```

The orchestrator is fully deterministic: all nondeterminism is pushed into the injected `RunnerCallable`.

## Flow 7: Ingress / egress (intended, not currently functional)

```mermaid
flowchart LR
    A[External Request] --> B[DNI-2 Atmosphere]
    B -->|PERMIT| C[Crust / Application]
    B -->|QUARANTINE| D[Quarantine]
    B -->|REJECT| E[Reject + log]
    B -->|DEFER| F[Hold queue]
    C --> G[Core execution]
    G --> H[Egress envelope]
    H --> B
    B -->|PERMIT| I[External response]
```

Note: `interface/dni_2_quarantine.py` currently has broken imports and cannot be imported. The design intent is documented here.

## Cross-cutting concerns

| Concern | Where enforced |
|---|---|
| Wall-clock exclusion | Executor, ZarcJournal, SQLite persistence, telemetry collector, panelmesh ticks |
| Canonical JSON | `shared/canonical.py` |
| Ed25519 signatures | `kernel/provenance.py`, `governance/dag_signer.py` |
| Deterministic identity | `derive_execution_id`, `stable_event_id_from_components` |
| Idempotency | `IIdempotencyStore`, replay-safe `.zarc` writes |
| Audit artifact single-sourcing | `domain/semantics/derivations.py` |
| Governance non-bypassability | `CBI0OrchestratedExecutor`, `ExecutionAuthority` |
