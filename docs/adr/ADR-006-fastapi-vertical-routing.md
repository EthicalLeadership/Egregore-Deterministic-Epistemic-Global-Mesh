# ADR-006: FastAPI HTTP API with Vertical Routing

## Status

Accepted

## Context

Egregore exposes dossier generation, document intake, chat completions, and
workflow health endpoints. The API must support both default deterministic
processing and vertical-specific inference policies.

## Decision

Use FastAPI with a thin HTTP layer (`src/egregore/http_api/http/app.py`).
Application facades (`src/egregore/application/http_v1_service.py` and
`src/egregore/application/http_v1_facades.py`) own case seeding and vertical
selection. Vertical routing is selected by the `vertical` field; when absent,
the default deterministic policy is used.

## Consequences

- **Positive:** HTTP layer stays thin and testable.
- **Positive:** Vertical logic is injected at the application boundary.
- **Negative:** Vertical-specific model catalogs require runtime configuration.

## Stakeholder Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Architecture Lead | Samuel Tessier | 2026-06-18 | Approved |
| Security Lead | Samuel Tessier | 2026-06-18 | Approved |
| SRE Lead | Samuel Tessier | 2026-06-18 | Approved |