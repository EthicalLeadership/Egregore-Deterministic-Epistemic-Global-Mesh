# 12 — Glossary

## .zarc

Egregore's append-only, signed, hash-chained JSONL audit log. Each line is a `ZarcEntry` with `ts_ns`, `engine`, `event`, `payload`, `prev_hash`, and `sig`. The `.zarc` file is the single system of record.

## Agency

A runtime actor on the crust. Identified by `(species, biome, lobe, instance_tag)`. Processes work units and consumes energy.

## ANCHORUM

External vault/audit system. Egregore bridges `.zarc` entries into ANCHORUM via injected callables (`AnchorumBridge`, `LitigationHoldTrigger`).

## Biome

An environment where a species operates: `RESEARCH`, `FORTRESS`, `WILDERNESS`, `FACTORY`, `GARDEN`.

## BIOK

Boundary of Known/Kernel of integrity. The structural validation boundary that legal-agent outputs must cross.

## CBI-0

Constraint-Binding Interface 0. The four-hook governance surface:

- M1 — projection access scope enforcement
- M2 — registry completeness/consistency
- M3 — terminal output non-re-entry
- M4 — spec/runtime equivalence audit emission

## Canonical IR

`CanonicalSemanticIR` — the typed intermediate representation that can express only `FACT`, `CLASSIFICATION`, `EVIDENCE_INTERPRETATION`, and `HYPOTHESIS`. Legal conclusions are structurally unrepresentable.

## Canonical JSON

Deterministic JSON serialization: sorted keys, no whitespace, UTF-8 safe, NaN/Inf rejected. Defined in `shared/canonical.py`.

## Crust

The geological layer where agencies live. Corresponds to `application/` and `domain/agency_taxonomy.py`.

## Determinism

The property that the same inputs + versions produce the same outputs + audit trail. Core Plane is wall-clock-free; timestamps are injected.

## DNI-2

Atmosphere border / quarantine subsystem. Intended to inspect ingress/egress work units and enforce energy/sensitivity policies.

## Dynamo

The geological layer of governance and audit. Corresponds to `governance/`.

## Execution authority

`ExecutionAuthority` context manager. Legal-agent execution is only permitted inside a governed scope.

## Fossil

Immutable record of a dead agency in the sediment archive.

## Gate 5

The observable-envelope model: `π_O` (projection operator) and `≡_O` (equivalence relation) determine semantic identity and replay invariance.

## Gearbox

Deterministic thermal/throughput gear selector. Gears: `G0`, `G2`, `G5`.

## Idempotency fingerprint

`derive_execution_id(command)` — a SHA256 over the version-aware command identity lattice. Used to detect duplicate requests.

## IRField

Canonical structural field address over `CanonicalSemanticIR`: `ENTITY_TYPE`, `ATTRIBUTE`, `EVIDENCE_BLOCK`, `RELATION`, `INFERENCE_NODE`, `METADATA_BLOCK`.

## Lobe

Functional division within a species: `COGNITION`, `MEMORY`, `PERCEPTION`, `ACTION`, `METABOLISM`.

## Mantle

The geological layer of ops/energy/chaos/backpressure. Corresponds to `domain/ops/` and `infrastructure/ops/` (mostly missing).

## Mycelium

Inter-agency messaging network. Conceptual stub in `interface/mycelial_network.py`.

## Noyau

The inner core: cryptographic identity and `.zarc` format. Corresponds to `kernel/`.

## Observable envelope

`ObservableEnvelope` partitioned into `execution`, `case`, `policy`, and `evaluator` metadata. Used for Gate 5 semantic invariance checks.

## Outbox entry

`OutboxEntry` — a durable side-effect intent produced by artifact derivation.

## Panel

A schedulable unit in the DT1 panelmesh runtime. Panels transition through `QUEUED → LEASED → RUNNING → SUCCEEDED/FAILED/TIMED_OUT → ARCHIVED`.

## Panelmesh

The DT1 distributed scheduling mesh. Phase 1 is implemented in `dt1/panelmesh_phase1_runtime.py` and `dt1/panelmesh_phase1_orchestrator.py`.

## Plane 1

Authoritative core: `kernel/`, `domain/`, `application/`, `governance/`. Deterministic, immutable, never imports Plane 2.

## Plane 2

Projection / IO layer: `interface/`, `infrastructure/`, `http_api/`, `bus/`, `cortex/`, `dt1/`, `powertrain/`. Observable, replaceable.

## Projection descriptor

`ProjectionDescriptor` — registered contract describing which `IRField`s an agent may observe and what constraints apply.

## Provenance event

`ProvenanceEvent` — infrastructure-agnostic domain event shape. Carries `engine`, `event`, `payload`, `ts_ns`.

## Sediment

Immutable fossil archive. Corresponds to `shared/` and `infrastructure/sediment_archive.py`.

## Species

Agency classification: `ACADEMIC`, `DEFENSIVE`, `INTELLIGENCE`, `PRODUCTIVE`, `USELESS`.

## Stratigraphy

Layering of fossils by epoch in the sediment archive.

## Thermal governor

Throughput regulator that shifts gearbox gears based on temperature, VRAM, and queue depth; emits `.zarc` events on G5.

## Two-plane separation

The rule that Plane 1 (authoritative core) never imports Plane 2 (projection/IO). Enforced by AST tests.

## Work unit

`WorkUnit` in DT1 — the unit of distributed work, carried end-to-end with deterministic trace identity.

## ZarcJournal

`ZarcJournal` — event-sourced persistence adapter that rebuilds read models by replaying the `.zarc` chain.
