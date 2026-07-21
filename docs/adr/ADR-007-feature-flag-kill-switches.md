# ADR-007: In-Memory Feature Flag Registry for Kill Switches

## Status

Accepted

## Context

Production incidents may require disabling a capability faster than a full code
rollback. We need a lightweight kill-switch mechanism that integrates with the
existing guard policy checks.

## Decision

Use an in-memory `FeatureFlagRegistry` per process. Flags support tenant and
role scoping. In production this can be replaced by a persistence-backed
implementation through the same port interface.

Implementation: `src/egregore/application/feature_flag_registry.py`.

## Consequences

- **Positive:** Fast incident response via capability toggles.
- **Positive:** Simple interface compatible with guard policies.
- **Negative:** Flags are process-local; rolling restarts are needed for global
  propagation unless a remote backing store is added.

## Stakeholder Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Architecture Lead | Samuel Tessier | 2026-06-18 | Approved |
| Security Lead | Samuel Tessier | 2026-06-18 | Approved |
| SRE Lead | Samuel Tessier | 2026-06-18 | Approved |