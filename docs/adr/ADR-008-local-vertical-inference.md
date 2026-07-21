# ADR-008: Local Vertical Inference with Constrained Semantic Engine

## Status

Accepted

## Context

Vertical-specific dossier generation needs local model inference without
relying on external cloud APIs. The inference must stay within the evidence-to-conclusion boundary enforced by the reasoning guard.

## Decision

Use a `ConstrainedSemanticEngine` combined with a local model catalog. The
compute engine policy is selected per vertical; if no catalog is configured,
the system falls back to a deterministic policy. Model inference is governed
by the same CBI-0 checkpoints as the rest of the core.

Implementation: `src/egregore/application/local_vertical_inference.py` and
`src/egregore/application/constrained_semantic_engine.py`.

## Consequences

- **Positive:** Air-gapped inference capability.
- **Positive:** Deterministic fallback when models are unavailable.
- **Negative:** Requires local model manifest and compatible GGUF catalog.

## Stakeholder Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Architecture Lead | Samuel Tessier | 2026-06-18 | Approved |
| Security Lead | Samuel Tessier | 2026-06-18 | Approved |
| SRE Lead | Samuel Tessier | 2026-06-18 | Approved |