# 03 — Domain Model

Status: `implemented & tested`

The domain layer is **pure**: no imports from `application/` or `infrastructure/`, no filesystem or network calls, no `open()`, no `pathlib`. It defines the canonical data structures and semantic contracts that the rest of the system must obey.

## Core principle: make illegal states unrepresentable

The domain model is designed so that forbidden concepts cannot be constructed through the type system. The canonical semantic IR, for example, has no constructor for a legal conclusion. A `LegalAnalysisOutput` structurally cannot contain prohibited conclusions.

## Canonical Semantic IR

Source: `src/egregore/domain/semantics/canonical_ir.py`.

The **CanonicalSemanticIR** is the intermediate representation for all semantic outputs. It can express exactly four statement types:

| Statement type | Purpose | Example |
|---|---|---|
| `FACT` | Verifiable factual claims | `"Email sent 2024-01-05"` |
| `CLASSIFICATION` | Routing / system classification | `"route_to_legal"` with confidence |
| `EVIDENCE_INTERPRETATION` | Bounded interpretation | `"may indicate pattern X"` |
| `HYPOTHESIS` | Speculative claim pending verification | `"claim Y if evidence Z holds"` |

**Deliberately absent:** `LEGAL_CONCLUSION`. The IR makes legal determinations structurally unrepresentable.

Key types:

- `CanonicalSemanticIR` — versioned container of statements.
- `SemanticStatement` — union of the four allowed statement dataclasses.
- `validate_semantic_ir_contract(ir)` — defensive runtime validation.

Each IR carries `m1_accessed_fields`: a `frozenset[IRField]` used by CBI-0 M1 to validate that an agent accessed only its declared projection scope.

## IR field vocabulary

Source: `src/egregore/domain/semantics/projection_descriptor.py`.

`IRField` defines semantic addresses over the IR:

- `ENTITY_TYPE` — statement type discrimination.
- `ATTRIBUTE` — content-bearing fields.
- `EVIDENCE_BLOCK` — evidence interpretation presence/content.
- `RELATION` — relationships between statements (not used in IR v1.0).
- `INFERENCE_NODE` — output-side inference fields.
- `METADATA_BLOCK` — top-level IR metadata.

These fields are used in projection descriptors to declare what an agent is allowed to observe.

## Projection descriptors

Source: `src/egregore/domain/semantics/projection_descriptor.py`.

A `ProjectionDescriptor` is a registered, immutable contract for an agent:

- `agent_id` + `version` — unique registry key.
- `scope` — set of `IRField` addresses the agent may observe.
- `constraints` — behavioral restrictions (e.g., `read_only`, `evidence_bounded`, `no_derived_output`).
- `sensitivity_level` — `STANDARD`, `RESTRICTED`, or `SENSITIVE`.
- `canonical_hash()` — stable SHA256 for ledger commitments.

`OverlapClassification` describes how two agents overlap:

- `DISJOINT`, `EQUIVALENT`, `DEPENDENT`, `INTERFERENCE_PRONE`, `CONFLICT_SENSITIVE`.

`BindingAuditRecord` captures the result of a CBI-0 M4 equivalence sweep.

Legal Agent v1 has a static descriptor in `src/egregore/domain/legal_agent/projection_registry.py`.

## Deserialization boundary

Source: `src/egregore/domain/semantics/ir_deserialization.py`.

`deserialize_to_canonical_ir(...)` is the trusted boundary from untrusted compute output to typed IR:

- Rejects forbidden top-level keys (`legal_conclusions`, `liability`, etc.).
- Rejects forbidden statement keys.
- Rejects forbidden legal-conclusion phrasing in interpretation statements.
- Computes `m1_accessed_fields` inline so CBI-0 M1 can validate without a second IR walk.

This is where the runtime converts raw engine output into a governable canonical form.

## Canonical event envelope

Source: `src/egregore/domain/semantics/canonical_event_envelope.py`.

`CanonicalEventEnvelope` binds deterministic metadata to events and outbox entries:

- `envelope_id` / `correlation_id` — derived from `command.request_id` or `command.causality_id`.
- `logical_timestamp_ns` — injected timestamp.
- `producer_identity` — constant for a given producer (default `"core_plane"`).
- `envelope_schema_version` — schema version for replay contract.

`build_canonical_event_envelope(...)` and `canonical_event_envelope_payload(...)` are used by artifact derivation and replay validation to ensure event/outbox identities are stable across live execution and replay.

## Evidence-to-conclusion boundary

Source: `src/egregore/domain/semantics/reasoning_guard.py`.

`enforce_evidence_to_conclusion_boundary(payload)` is a second defense layer:

- Rejects forbidden fields (`legal_conclusion`, `liability`, `wrongdoing_confirmed`).
- Detects forbidden phrasing ("establishes liability", "proves wrongdoing", etc.).
- In normal mode: downgrades forbidden phrasing to `"May indicate: additional evidence review is required; no legal determination is expressed."`
- In reject-only mode (`excluded_layer` present and non-empty): raises `ValueError`.

This is a runtime linguistic guard, not a type-system guard. It complements the IR's structural prohibition.

## Semantic observability envelope

Source: `src/egregore/domain/semantics/observability.py`.

Gate 5 defines an **observable envelope** model:

- `ObservableEnvelope` — partitioned into `execution`, `case`, `policy`, and `evaluator` metadata.
- `PI_O` — canonical projection operator.
- `EQUIV_O` — canonical equivalence relation.

The projection operator extracts a semantic identity payload that excludes evaluator metadata. The equivalence relation compares two envelopes by their projected identity. This allows replay to ignore semantic-neutral changes (e.g., evaluator alias renaming) while still detecting real semantic drift.

Core envelope keys:

- `execution`: `snapshot_hash`, `event_ids`, `event_seq`, `outbox_ids`, `admissibility_classification`
- `case`: `organization_id`, `case_id`, `causality_attribution_class`, `archive_stability_classification`
- `policy`: `policy_version`, `policy_level`, `routing_outcome_class`

## Agency taxonomy

Source: `src/egregore/domain/agency_taxonomy.py`.

The crust is populated by agencies. Each agency is identified by:

- `Species`: `ACADEMIC`, `DEFENSIVE`, `INTELLIGENCE`, `PRODUCTIVE`, `USELESS`
- `Biome`: `RESEARCH`, `FORTRESS`, `WILDERNESS`, `FACTORY`, `GARDEN`
- `Lobe`: `COGNITION`, `MEMORY`, `PERCEPTION`, `ACTION`, `METABOLISM`
- `instance_tag`: free-form instance label

`AgencyState` tracks energy, work units, lifecycle timestamps, and sediment id.

`CrustPopulation` registers, kills, and fossilizes agencies.

## Dossier / event models

Source: `src/egregore/domain/models/dossier.py`, `src/egregore/domain/models/event.py`.

`Dossier` is an immutable aggregate:

- `dossier_id`, `case_id`, `version`, `intent_hash`
- `state: DossierState` (list of `Event`)
- `canonical_state` — authoritative serialized JSON
- `timestamp_ns`, `signature`

`Event` is a simple domain event: `event_type`, `payload`, `timestamp_ns`.

## Semantics models

Source: `src/egregore/domain/semantics_models.py`.

Central command/event/value types:

| Type | Purpose |
|---|---|
| `CaseState` | `created`, `active`, `generating`, `versioned`, `archived` |
| `TaskExecutionState` | `INIT`, `VALIDATE`, `PLAN`, `EXECUTE`, `VERIFY`, `COMMIT`, `ARCHIVE` |
| `TaskContract` | Intent, constraints, inputs, allowed tools, expected outputs |
| `GenerateDossierCommand` | Full command for dossier generation; includes `input_fingerprint`, `engine_version`, `policy_version`, `causality_id` |
| `DossierSnapshot` | Versioned snapshot result |
| `AuditEvent` | Domain audit event with schema version and sequence |
| `OutboxEntry` | Side-effect outbox entry |
| `CommandResult` / `CommandAck` | Execution result and HTTP-level ack |
| `StableErrorCode` / `SemanticsError` | Fail-closed error model |

## Legal-agent domain model

Source: `src/egregore/domain/legal_agent/legal_models.py`.

- `LegalFact` — normalized legal primitive projected from IR.
- `RuleMatch` — a rule matched to facts.
- `InferenceNode` — one reasoning step; conclusion is evidence-bounded only.
- `LegalAgentVersion` — rule registry + inference engine version.
- `LegalAnalysisOutput` — terminal output; `prohibited_conclusions` is structurally `()`.

## Rule registry

Source: `src/egregore/domain/legal_agent/rule_registry.py`.

`StaticRuleRegistry` is a Phase 1 in-code registry of five general legal rules:

- workplace communications
- document retention
- timeline evidence
- confidentiality obligation
- adverse action proximity

Rules match facts via trigger keyword presence in lowercased fact content. The registry implements `IRuleRegistry` from `interface/legal_agent_ports.py` so it can be substituted later.

## Execution authority

Source: `src/egregore/domain/legal_agent/execution_authority.py`.

`ExecutionAuthority` is a context-var-based sovereignty gate:

- `governed()` context manager increments a depth counter.
- `assert_governed()` raises if depth is zero.
- Only calls inside a governed scope may run `LegalReasoningEngine.analyze()`.

This ensures legal-agent execution cannot happen accidentally or from an unauthorized call path.

## Provenance event

Source: `src/egregore/domain/provenance_model.py`.

`ProvenanceEvent` is the infrastructure-agnostic domain event shape:

- `engine`, `event`, `payload`, `ts_ns`

It does **not** include `prev_hash` or `sig`; those are storage-layer responsibilities.

## Hardware work unit types

Source: `src/egregore/domain/hardware_work_unit.py`.

Types used by the DT1/powertrain runtime:

- `GearMode` — `TURBO`, `ECO`, `EMERGENCY_FREEZE`.
- `PrecisionGear` — `FP16`, `FP32`, `INT8`.
- `DTProfile` — deterministic thermal/device profile snapshot.
- `TurbineUnit` — logical worker identity.
- `WorkPayload` — batch unit of matrix work.
- `domain_fingerprint()` / `canonical_fingerprint()` — deterministic canonical JSON fingerprints.

## Domain adapters

Source: `src/egregore/domain/semantics/domain_adapters.py`.

`DossierSemanticsDomainAdapter` is the default `ISemanticsDomainAdapter`. It names the event types and outbox side-effect type for dossier generation:

- `requested_event_type()` → `"DOSSIER_GENERATION_REQUESTED"`
- `generated_event_type()` → `"DOSSIER_GENERATED"`
- `outbox_side_effect_type()` → `"GOVERNANCE_INGEST"`

## Artifact derivation

Source: `src/egregore/domain/semantics/derivations.py`.

`derive_generate_artifacts(...)` is the **single source of truth** for turning a deterministic compute result into:

- one `DossierSnapshot`
- two `AuditEvent`s (`DOSSIER_GENERATION_REQUESTED`, `DOSSIER_GENERATED`)
- one `OutboxEntry`

Architecture tests enforce that `AuditEvent(...)` and `OutboxEntry(...)` constructor calls live only in this module. This prevents orchestration code from constructing audit artifacts ad-hoc.

The module also provides `journal_deserialize_audit_events()` and `journal_deserialize_outbox_entries()` for replay reconstruction.

## Invariants

| Invariant | Enforcement |
|---|---|
| Legal conclusions unrepresentable | `CanonicalSemanticIR` statement union excludes them. |
| Agent projection scope declared | `ProjectionDescriptor.scope` is a `frozenset[IRField]`. |
| Legal agent only runs governed | `ExecutionAuthority.assert_governed()`. |
| Audit events single-sourced | AST tests enforce constructor calls only in `derivations.py`. |
| Domain purity | AST tests forbid `application/` and `infrastructure/` imports; forbid `pathlib`, `os`, `socket`, `open()`, etc. |

## Tests

- `tests/test_canonical_ir.py`
- `tests/test_reasoning_guard.py`
- `tests/test_cbi_projection_binding.py`
- `tests/test_cbi_0_enforcement.py`
- `tests/test_execution_authority.py`
- `tests/test_architecture_policy_intent.py`
- `tests/test_arch_enforcement.py`
