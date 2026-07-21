"""Deterministic verification tooling used by the Reproducible Fusion Engine.

This module exposes the canonical hashing, serialization, and replay-determinism
helpers that the RFE relies on to guarantee idempotent, byte-identical output.
It intentionally wraps existing Egregore kernel/application primitives so that
"deterministic verification" is a single import surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from egregore.application.replay_determinism import (
    DeterminismViolationError,
    ReplayDeterminismMetadata,
    ReplayDeterminismValidator,
    ReplayValidationResult,
)
from egregore.shared.canonical import canonical_dumps, canonical_loads, sha256_hex

__all__ = [
    "canonical_dumps",
    "canonical_loads",
    "sha256_hex",
    "fingerprint_canonical",
    "ReplayDeterminismMetadata",
    "ReplayDeterminismValidator",
    "ReplayValidationResult",
    "DeterminismViolationError",
    "DeterministicVerifier",
]


def fingerprint_canonical(obj: Any) -> str:
    """Return a deterministic SHA-256 hex fingerprint of any canonical object."""
    return sha256_hex(canonical_dumps(obj).encode("utf-8"))


class DeterministicVerifier:
    """High-level deterministic verification wrapper for RFE outputs.

    - ``hash_report`` produces a stable SHA-256 over the canonical report dict.
    - ``hash_decision_log`` produces a stable SHA-256 over the canonical decision log.
    - ``validate_replay`` compares two runs using ReplayDeterminismValidator.
    """

    _validator: ReplayDeterminismValidator

    def __init__(self) -> None:
        self._validator = ReplayDeterminismValidator()

    def hash_report(self, report: Mapping[str, Any]) -> str:
        """Deterministic SHA-256 fingerprint of the report structure."""
        return fingerprint_canonical(dict(report))

    def hash_decision_log(self, decision_log: Mapping[str, Any]) -> str:
        """Deterministic SHA-256 fingerprint of the decision log."""
        return fingerprint_canonical(dict(decision_log))

    def record_execution(
        self,
        *,
        engine_version: str,
        policy_version: str,
        reasoning_version_id: str,
        input_fingerprint: str,
        output_fingerprint: str,
    ) -> ReplayDeterminismMetadata:
        """Record determinism metadata from a live RFE execution."""
        return self._validator.record_execution(
            engine_version=engine_version,
            policy_version=policy_version,
            reasoning_version_id=reasoning_version_id,
            input_fingerprint=input_fingerprint,
            output_fingerprint=output_fingerprint,
        )

    def validate_replay(
        self,
        *,
        original: ReplayDeterminismMetadata,
        replayed_engine_version: str,
        replayed_policy_version: str,
        replayed_reasoning_version_id: str,
        replayed_input_fingerprint: str,
        replayed_output_fingerprint: str,
    ) -> ReplayValidationResult:
        """Validate that a replayed RFE execution matches the original."""
        return self._validator.validate_replay(
            original=original,
            replayed_engine_version=replayed_engine_version,
            replayed_policy_version=replayed_policy_version,
            replayed_reasoning_version_id=replayed_reasoning_version_id,
            replayed_input_fingerprint=replayed_input_fingerprint,
            replayed_output_fingerprint=replayed_output_fingerprint,
        )


def load_canonical_file(path: str | Path) -> Any:
    """Load a canonical JSON file deterministically."""
    return canonical_loads(Path(path).read_text(encoding="utf-8"))
