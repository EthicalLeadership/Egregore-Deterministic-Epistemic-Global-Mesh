# ADR-001: Use ZARC Signed Provenance Chain for Durable State

## Status

Accepted

## Context

Egregore must persist legal dossier state in a way that supports audit,
replay, and tamper detection. Traditional relational-only persistence does not
provide an immutable, verifiable history of how each state was produced. We
need a single source of truth that can be replayed deterministically and
verified cryptographically.

## Decision

Use an append-only `.zarc` chain as the durable journal. Every commit is
serialized to canonical JSON, signed, and appended with the previous hash.
Read models (case store, idempotency store, snapshot cache) are rebuilt by
replaying the chain on startup.

Implementation: `src/egregore/infrastructure/zarc_journal.py` and
`src/egregore/kernel/provenance.py`.

## Consequences

- **Positive:** Immutable history, deterministic replay, signature verification,
  tamper detection.
- **Positive:** Read models can be reconstructed from the chain.
- **Negative:** Slightly higher storage cost than relational-only.
- **Negative:** All commits must inject `timestamp_ns` deterministically; no
  wall-clock timestamps are allowed inside the journal.

## Stakeholder Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Architecture Lead | Egregor | 2026-06-18 | Approved |
| Security Lead | Egregor | 2026-06-18 | Approved |
| SRE Lead | Egregor | 2026-06-18 | Approved |