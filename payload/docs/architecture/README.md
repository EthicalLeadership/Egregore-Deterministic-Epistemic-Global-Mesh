# Egregore Architecture Blueprints

This directory contains the authoritative, source-code-backed architecture documentation for the Egregore runtime skeleton.

## How to read these blueprints

Each document is designed to answer four questions:

1. **What** is this subsystem?
2. **Where** does it live in the codebase?
3. **How** does it work?
4. **Why** does it exist — what invariant or guarantee does it protect?

Documents use status badges to mark implementation health:

| Badge | Meaning |
|---|---|
| `implemented & tested` | Code exists and has passing tests |
| `implemented with defects` | Code exists but has known issues, failing tests, or policy violations |
| `stub` | Module exists but is mostly empty or placeholder |
| `missing` | Documented concept has no implementation yet |

## Document index

| # | Document | Focus |
|---|---|---|
| 0 | [Executive Summary](00_executive_summary.md) | What Egregore is, its axioms, and its purpose |
| 1 | [Geological Model](01_geological_model.md) | Layered architecture, two-plane separation, communication rules |
| 2 | [Zarc Provenance](02_zarc_provenance.md) | The `.zarc` audit log: format, signing, chain, replay |
| 3 | [Domain Model](03_domain_model.md) | Canonical IR, agency taxonomy, dossier/event models |
| 4 | [Application Runtime](04_application_runtime.md) | Executors, CBI-0 orchestration, replay, versioning |
| 5 | [Governance & Audit](05_governance_audit.md) | CBI-0, ExecutionAuthority, ANCHORUM, litigation hold |
| 6 | [Infrastructure Adapters](06_infrastructure_adapters.md) | Persistence, LLM catalog, telemetry, sediment archive |
| 7 | [Interfaces & Transport](07_interfaces_transport.md) | Ports, HTTP API, mycelial network, quarantine |
| 8 | [Powertrain & DT1](08_powertrain_dt1.md) | Gearbox, thermal governor, panelmesh runtime |
| 9 | [Data Flows](09_data_flows.md) | End-to-end flows with diagrams |
| 10 | [Testing & Governance](10_testing_governance.md) | Architecture-policy tests and layer rules |
| 11 | [Repository State](11_repository_state.md) | Tracked/untracked status, defects, gaps |
| 12 | [Glossary](12_glossary.md) | Terms and definitions |
| 13 | [Global Event Schema](13_global_event_schema.md) | Canonical event record every service must emit |

## Core source tree

```
src/egregore/
├── kernel/            # Noyau: cryptographic provenance, .zarc format
├── domain/            # Pure domain: IR, agencies, dossiers, legal models
├── application/       # Orchestrators, executors, services
├── governance/        # CBI-0, ANCHORUM, litigation hold, audit
├── interface/         # Ports and transport contracts
├── infrastructure/    # Persistence, adapters, telemetry, sediment
├── http_api/          # FastAPI HTTP surface
├── bus/               # NATS/JetStream broker
├── cortex/            # Pulse agents (observability)
├── dt1/               # Deterministic runtime / panelmesh
├── powertrain/        # Gearbox & thermal governor
├── shared/            # canonical JSON, stable IDs
└── models/            # Shared data models (user, etc.)
```

## Contributing to these docs

Every factual claim should cite a concrete source file path. When the code changes, update the corresponding blueprint.
