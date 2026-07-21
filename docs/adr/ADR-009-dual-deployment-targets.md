# ADR-009: Dual Deployment Targets — systemd and Docker

## Status

Accepted

## Context

Egregore operates in two distinct environments: Pioneer 1, a bare-metal or VM
host managed with systemd, and Client Red Dart, a containerized deployment.
Both must use the same source artifacts and pass the same gates.

## Decision

Maintain two first-class deployment paths:

- **Pioneer 1:** npm-first local development and systemd production service.
- **Client Red Dart:** Docker Compose with non-standard host ports to avoid
  collision with npm local services.

Both paths use the same `pyproject.toml`, `Dockerfile`, `docker-compose.yml`,
and gate suite.

## Consequences

- **Positive:** Flexibility across client environments.
- **Positive:** Docker ports do not conflict with local npm services.
- **Negative:** Two deployment targets require dual smoke testing.

## Stakeholder Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Architecture Lead | Samuel Tessier | 2026-06-18 | Approved |
| Security Lead | Samuel Tessier | 2026-06-18 | Approved |
| SRE Lead | Samuel Tessier | 2026-06-18 | Approved |