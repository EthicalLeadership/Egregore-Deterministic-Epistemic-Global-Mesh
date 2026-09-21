# ADR-004: Deterministic Timestamps and Replay Invariants

## Status

Accepted

## Context

Dossier generation must be reproducible for audit and legal defensibility. A
replay must produce the same canonical output from the same inputs, even if
the internal representation drifts (e.g., `list` vs `tuple`).

## Decision

- Core plane accepts or derives `timestamp_ns` deterministically.
- `DossierGenerateService` derives `timestamp_ns` from a SHA-256 hash of the
  command fields when the caller does not supply one.
- Replay invariants are verified by `tests/test_gate5_invariants.py`, which
  tolerates representational drift that preserves canonical equivalence.

Implementation: `src/egregore/application/dossier_generate_service.py` and
`tests/test_gate5_invariants.py`.

## Consequences

- **Positive:** Reproducible builds, replay-based audits, deterministic tests.
- **Positive:** Representational drift does not break replay equivalence.
- **Negative:** Callers must not rely on wall-clock timestamps for identity.

## Stakeholder Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Architecture Lead | Egregor | 2026-06-18 | Approved |
| Security Lead | Egregor | 2026-06-18 | Approved |
| SRE Lead | Egregor | 2026-06-18 | Approved |