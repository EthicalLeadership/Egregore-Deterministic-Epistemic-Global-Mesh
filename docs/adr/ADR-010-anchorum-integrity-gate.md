# ADR-010: Anchorum Integrity Gate for Pre-Deploy Verification

## Status

Accepted

## Context

Before any deploy or update cycle, we must verify that critical source files,
imports, adapters, domain models, KEK, GGUF catalog, host hardening, and
backups are healthy. A failure should detect a fork and halt deployment.

## Decision

Run the `AnchorumIntegrityGate` before deploy. It checks file existence,
import integrity, PostgreSQL adapter smoke test, domain model integrity, KEK
health, GGUF catalog health, dedup state, UFW, fail2ban, backups, and
WireGuard. On failure it reports to a `FreezeController`.

Implementation: `src/egregore/governance/anchorum_integrity_gate.py`.

## Consequences

- **Positive:** Prevents deployment when the environment or code base is unhealthy.
- **Positive:** Integrates with freeze-state detection.
- **Negative:** Requires host-level tools (`ufw`, `fail2ban`, `wg`) to be present
  for all checks to pass.

## Stakeholder Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Architecture Lead | Samuel Tessier | 2026-06-18 | Approved |
| Security Lead | Samuel Tessier | 2026-06-18 | Approved |
| SRE Lead | Samuel Tessier | 2026-06-18 | Approved |