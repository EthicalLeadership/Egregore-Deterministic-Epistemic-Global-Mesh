# ADR-002: Layered Architecture with Enforced Dependency Boundaries

## Status

Accepted

## Context

Egregore is a Python+FastAPI system with legal-data workflows. Without
explicit dependency rules, infrastructure concerns leak into domain logic and
make the system harder to test, audit, and deploy.

## Decision

Adopt a strict layered architecture:

- **domain** — business logic and models; may depend only on `interface` and `shared`.
- **application** — use cases and orchestration; may depend on `domain`,
  `interface`, `http_api`, `kernel`, `models`, `powertrain`, `services`, and `shared`.
- **infrastructure** — adapters; may depend on `domain`, `interface`, `kernel`, and `shared`.
- **interface** — port protocols; may depend on `domain`.
- **http_api** — transport; may depend on `application`, `domain`, and `models`.

Dependency rules are enforced by `tests/test_arch_enforcement.py`.

## Consequences

- **Positive:** Clear ownership, testable core, reduced blast radius.
- **Positive:** Domain layer remains pure (no filesystem, network, or subprocess calls).
- **Negative:** New cross-layer imports require whitelist updates and board review.

## Stakeholder Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Architecture Lead | Egregor | 2026-06-18 | Approved |
| Security Lead | Egregor | 2026-06-18 | Approved |
| SRE Lead | Egregor | 2026-06-18 | Approved |