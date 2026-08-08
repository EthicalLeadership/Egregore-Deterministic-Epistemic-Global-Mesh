# 05 — Governance & Audit

Status: `implemented & tested`

The governance layer (Dynamo) protects the core with CBI-0 checkpoints, ANCHORUM bridge operations, and litigation-hold triggers. It is allowed to import only from `shared/`.

## Design intent

Governance must be **non-bypassable** and **auditable**. Every meaningful enforcement action either blocks execution or leaves a record. The layer does not perform business computation; it verifies that computation may proceed safely.

## CBI-0 Constraint Binding Interface

CBI-0 is the four-hook governance interface that binds declared projection constraints to runtime enforcement.

| Hook | Responsibility | Implementation |
|---|---|---|
| **M1** | Projection access scope enforcement | `application/cbi_0_projection_access_monitor.py` |
| **M2** | Registry completeness and consistency | `application/cbi_0_projection_registry_validator.py` |
| **M3** | Terminal output non-re-entry | `application/cbi_0_composition_guard.py` |
| **M4** | Spec/runtime equivalence audit emission | `application/cbi_0_binding_audit_emitter.py` |

The port definitions live in `src/egregore/interface/constraint_binding_ports.py`.

### M1 — Projection access monitor

`IProjectionAccessMonitor.declare()` registers an agent's descriptor. `validate_access()` checks that the accessed IR fields are a subset of the declared scope. Violation raises `ProjectionBindingError`.

Concrete implementation: `ProjectionAccessMonitor`.

### M2 — Projection registry validator

`IProjectionRegistryValidator.validate_registry()` checks:

- Every active agent has a descriptor.
- Every non-empty scope overlap has an `OverlapClassification`.
- Classifications are consistent with computed overlaps.

Concrete implementation: `ProjectionRegistryValidator`.

### M3 — Composition guard

`ICompositionGuard` has two methods:

- `assert_terminal(output, source_agent_id)` — records a terminal artifact fingerprint and rejects reuse.
- `assert_no_implicit_ir_synthesis(source_agent_id, target_input, target_type_name)` — rejects routing terminal output into `CanonicalSemanticIR` without a re-validation bridge.

Concrete implementation: `CompositionGuard`.

### M4 — Binding audit emitter

`IBindingAuditEmitter.emit_equivalence_sweep()` compares registry descriptor commitments against observed runtime state and emits a `BindingAuditRecord`:

- `registry_hash`
- `runtime_state_hash`
- `equivalence_status` (`EQUIVALENT` or `DIVERGED`)
- `divergence_details`
- `agent_id`, `binding_hook_id`

Concrete implementation: `MemoryBindingAuditEmitter`.

## CBI-0 runtime chain

Source: `src/egregore/application/cbi_0_orchestrated_executor.py`.

`enforce_cbi0_runtime_chain_for_legal_ir()` runs M2 → M1 → M4 for the legal agent over canonical IR. It is called by:

- `CorePlaneGenerateDossierExecutor.handle_generate_dossier()` (live path)
- `CorePlaneReplayInterpreter.replay_equivalence()` (replay path)

`CBI0OrchestratedExecutor.run_legal_agent_v1()` adds the governed execution of `LegalReasoningEngine.analyze()` and the M3 terminal-output guard.

## Execution authority

Source: `src/egregore/domain/legal_agent/execution_authority.py`.

`ExecutionAuthority` is a context-var-based gate. Legal reasoning may only run inside `ExecutionAuthority.governed()`. This is enforced by `LegalReasoningEngine.analyze()` calling `ExecutionAuthority.assert_governed()`.

Only three source files are allowed to reference `ExecutionAuthority`:

- `application/cbi_0_orchestrated_executor.py`
- `application/legal_reasoning_engine.py`
- `domain/legal_agent/execution_authority.py`

This is checked by `tests/test_arch_enforcement.py`.

## ANCHORUM bridge

Source: `src/egregore/governance/anchorum_bridge.py`.

`AnchorumBridge` converts `.zarc` JSONL entries into `VaultIngestRecord` batches and passes them to an injected `vault_ingest` callable.

Key properties:

- No direct ANCHORUM imports; fully injection-based.
- Reads the last `N` entries from a `.zarc` file.
- Packages each entry as `canonical_json(entry)` bytes with metadata.
- Designed for offline deterministic testing and CI.

## ANCHORUM ingest runner

Source: `src/egregore/governance/anchorum_ingest_runner.py`.

`run_anchorum_bridge_ingest()` runs the bridge end-to-end with an offline collector:

- `OfflineVaultIngestCollector` records batches in-memory.
- Returns an `IngestRunReport` with record views and ingestion metrics.
- Does **not** verify `.zarc` signatures/hash chains because the governance layer must not import `kernel/`.

CLI scripts referenced in `README.md`:

- `python3 anchorum_ingest.py --zarc ... --last-n ... --outdir ...`
- `python3 anchorum_compare.py --left ... --right ... --last-n ... --outdir ...`

## ANCHORUM ingest comparator

Source: `src/egregore/governance/anchorum_ingest_comparator.py`.

`compare_anchorum_ingests()` compares two `IngestRunReport` objects:

- Batch counts
- Tail payload diffs (canonical JSON per payload)
- Verdict: `MATCH`, `DIFF`, `FAIL_CHAIN_VERIFICATION`, `INCOMPLETE`, `unknown`

This supports reproducibility testing: two `.zarc` files produced from the same inputs should yield `MATCH`.

## DAG signer

Source: `src/egregore/governance/dag_signer.py`.

`DagSigner` provides Ed25519 signing/verification for governance DAG snapshots:

- `sign(payload)` returns `DagSignature(key_hex, sig_hex, digest_hex)`.
- `verify(payload, sig_hex)` verifies the signature.
- `merge_payload_for_signature()` merges digest and signature into a record.

It uses the same canonical JSON + Ed25519 primitives as the `.zarc` writer.

## Litigation hold trigger

Source: `src/egregore/governance/litigation_hold.py`.

`LitigationHoldTrigger` is a thin wrapper around an injected ANCHORUM litigation-hold API callable:

- `trigger(case_id, scope, reason)` delegates to the callable.
- Returns a string hold id.
- Keeps runtime dependencies optional and testable.

## Audit emission rules

- `AuditEvent` and `OutboxEntry` constructors may only be called from `domain/semantics/derivations.py`.
- `commit_generate_t2()` may only be called from `application/semantics_executor.py`.
- `canonical_json` / `sha256_hex` may only be defined in `shared/canonical.py`.
- `json.loads` / `json.dumps` may only be called from `shared/canonical.py`.

These rules are enforced by AST tests in `tests/test_architecture_policy_intent.py`.

## Invariants

| Invariant | Enforcement |
|---|---|
| CBI-0 non-bypassable | Executor and replay interpreter always call the chain. |
| Legal agent governed | `ExecutionAuthority.governed()` required. |
| Governance cannot import kernel | Architecture tests forbid `egregore.kernel` imports in `governance/`. |
| Audit artifacts single-sourced | Constructor calls restricted by AST tests. |
| ANCHORUM integration injection-only | `AnchorumBridge` and `LitigationHoldTrigger` take callables. |

## Tests

- `tests/test_cbi_0_runtime_chain.py`
- `tests/test_cbi_0_enforcement.py`
- `tests/test_cbi_projection_binding.py`
- `tests/test_execution_authority.py`
- `tests/test_anchorum_ingest_flow.py`
- `tests/test_arch_enforcement.py`
- `tests/test_architecture_policy_intent.py`
