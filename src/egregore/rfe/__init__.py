"""Reproducible Fusion Engine (RFE).

Idempotency definition
----------------------
A function is idempotent with respect to a report when, given the same manifest
and configuration versions, repeated executions produce byte-identical output:
the same report structure, the same decision log, and the same SHA-256 hashes.
The RFE achieves this by:

1. Using only deterministic inputs (manifest + versioned config).
2. Avoiding wall-clock time inside scoring; freshness is computed relative to
   the manifest's own ``timestamp``.
3. Canonical JSON serialization for all hashes and signatures.
4. No non-deterministic sampling or LLM text generation in the report path.
"""

from __future__ import annotations

from egregore.rfe.engine import reproducible_fusion

__all__ = ["reproducible_fusion"]
