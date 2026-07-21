# ADR-003: CBI-0 Governance Checkpoints (M1–M4) as Fail-Closed Policy

## Status

Accepted

## Context

Legal workflows require strong guarantees that agents access only declared data,
that projection bindings are complete, that terminal artifacts do not re-enter
core reasoning, and that runtime state matches the declared spec. Violations
must halt execution rather than degrade gracefully.

## Decision

Implement CBI-0 (Constraint Binding Interface — Checkpoint 0) with four
fail-closed checkpoints:

- **M1** — projection access scope enforcement.
- **M2** — registry completeness and overlap classification.
- **M3** — terminal non-reentry / no implicit IR synthesis.
- **M4** — spec/runtime equivalence audit.

Implementation: `src/egregore/governance/cbi0_governance.py`.
Tests: `tests/test_cbi_0_enforcement.py`.

## Consequences

- **Positive:** Violations are blocked before they can corrupt state.
- **Positive:** M4 produces an audit record with `EQUIVALENT` or `DIVERGED` status.
- **Negative:** Any checkpoint failure stops the operation; requires careful
  error handling and monitoring.

## Stakeholder Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Architecture Lead | Samuel Tessier | 2026-06-18 | Approved |
| Security Lead | Samuel Tessier | 2026-06-18 | Approved |
| SRE Lead | Samuel Tessier | 2026-06-18 | Approved |