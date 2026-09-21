# 00 — Executive Summary

## What Egregore is

Egregore is a **CPU-only, deterministic, governance-first runtime skeleton** for processing case/dossier workloads. It is designed around one immutable axiom:

> **The signed, hash-chained `.zarc` audit log is the single system of record.**

Every state change that matters is appended to `.zarc` as a cryptographically signed JSONL record. Read models, snapshots, idempotency caches, and outboxes are **projections** rebuilt by replaying the chain. The core runtime therefore has no hidden mutable state: if you have the `.zarc` file and the public verification key, you can reconstruct exactly what the system did and prove that no one tampered with it.

## Why Egregore exists

The project targets high-stakes workloads — legal case analysis, evidence dossiers, regulatory submissions — where the following properties are non-negotiable:

| Requirement | Egregore answer |
|---|---|
| **Auditability** | Every commit is a signed, chained `.zarc` entry. |
| **Determinism** | Core plane is wall-clock-free; timestamps are injected. |
| **Governance** | CBI-0 (Constraint-Binding Interface 0) hooks guard legal-agent execution. |
| **Integrity** | Canonical JSON + SHA256 chain + Ed25519 signatures. |
| **Replayability** | State is rebuilt from `.zarc`; no hidden DB mutations. |
| **Replaceability** | Plane 2 adapters (persistence, HTTP, LLM, telemetry) can be swapped without touching Plane 1. |

## Design axioms

1. **Two-plane separation**
   - **Plane 1 (Authoritative Core):** `kernel/`, `domain/`, `application/`, `governance/`
   - **Plane 2 (Projection / IO):** `interface/`, `infrastructure/`, `http_api/`, `bus/`, `cortex/`, `dt1/`, `powertrain/`
   - Plane 1 never imports Plane 2. This is enforced by AST tests.

2. **Immutable audit substrate**
   - The `.zarc` JSONL chain is append-only and signed.
   - Hash continuity (`prev_hash`) links every entry to its predecessor.

3. **Deterministic compute**
   - Core functions receive `timestamp_ns` as input.
   - `ZarcJournal` deliberately raises if a wall-clock timestamp is attempted.
   - Canonical JSON uses sorted keys, no whitespace, and rejects NaN/Inf.

4. **Fail-closed governance**
   - Legal-agent execution must run inside `ExecutionAuthority.governed()`.
   - The canonical semantic IR structurally prevents legal conclusions.
   - CBI-0 M1–M4 gates validate projection access, registry, composition, and audit emission.

## What is implemented vs. still forming

| Area | Status |
|---|---|
| `.zarc` provenance, signatures, replay | `implemented & tested` |
| Canonical semantic IR | `implemented & tested` |
| CBI-0 governance hooks | `implemented & tested` |
| Zarc journal / SQLite persistence | `implemented & tested` |
| Gearbox / thermal governor | `implemented & tested` |
| Panelmesh phase-1 runtime | `implemented & tested` |
| ANCHORUM ingest / comparison | `implemented & tested` |
| HTTP API | `implemented with defects` |
| PostgreSQL persistence adapter | `implemented with defects` |
| DNI-2 quarantine | `implemented with defects` (broken imports) |
| Mycelial network | `stub` |
| Mantle / ops layer (JIT, energy, chaos) | `missing` |

See [11 — Repository State](11_repository_state.md) for the full current-state analysis.

## Mental model

Think of Egregore as a **geological planet**:

- **Noyau (core):** solid iron; generates the magnetic field (cryptographic identity).
- **Dynamo (outer core):** liquid convection; protective field (governance, audit).
- **Mantle:** viscous flow; transfers heat but is opaque (ops/energy/chaos — mostly missing).
- **Crust:** rigid plates where life exists (agencies, orchestrators).
- **Atmosphere:** filters radiation and meteors (ingress/egress quarantine).
- **Sediment:** fossil record (immutable archive of dead agencies).
- **Mycelium:** underground fungal network (inter-agency messaging).

This metaphor is not decorative; it maps directly to package structure and import rules. See [01 — Geological Model](01_geological_model.md).

## Where to start

- To understand the runtime: read [04 — Application Runtime](04_application_runtime.md).
- To understand the audit substrate: read [02 — Zarc Provenance](02_zarc_provenance.md).
- To understand the rules that keep the code honest: read [10 — Testing & Governance](10_testing_governance.md).
