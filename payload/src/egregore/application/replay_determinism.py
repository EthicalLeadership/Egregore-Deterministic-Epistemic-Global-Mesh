"""Replay determinism validation for versioned policy execution.

Ensures that replayed commands produce identical results by:
- pin-pointing engine version, policy version, and reasoning version
- validating deterministic compute logic is version-stable
- detecting version mismatches that would invalidate replay
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayDeterminismMetadata:
    """Metadata required for faithful replay of a versioned decision.

    Immutable record of the exact versions used so replay can recreate
    the identical compute path and produce identical results.
    """

    engine_version: str
    policy_version: str
    reasoning_version_id: str

    # Deterministic hash of the input (for validation)
    input_fingerprint: str

    # Deterministic hash of the output (for validation)
    output_fingerprint: str


@dataclass(frozen=True)
class ReplayValidationResult:
    """Result of comparing original vs replayed execution."""

    matches: bool  # True if original == replayed
    engine_version_match: bool
    policy_version_match: bool
    reasoning_version_match: bool
    input_fingerprint_match: bool
    output_fingerprint_match: bool
    divergence_detail: str | None = None


class ReplayDeterminismValidator:
    """Validate that replayed execution matches original deterministically.

    Used during both live execution (to record metadata) and replay
    (to verify identical conditions and results).
    """

    def __init__(self) -> None:
        pass

    def record_execution(
        self,
        *,
        engine_version: str,
        policy_version: str,
        reasoning_version_id: str,
        input_fingerprint: str,
        output_fingerprint: str,
    ) -> ReplayDeterminismMetadata:
        """Record determinism metadata from a live execution.

        Called after successful command execution to capture the
        exact versions and fingerprints needed for replay.
        """
        return ReplayDeterminismMetadata(
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
        """Validate that a replayed execution matches the original.

        Returns detailed validation result showing which versions/fingerprints match.
        All must match for replay to be considered equivalent.
        """
        engine_match = original.engine_version == replayed_engine_version
        policy_match = original.policy_version == replayed_policy_version
        reasoning_match = original.reasoning_version_id == replayed_reasoning_version_id
        input_match = original.input_fingerprint == replayed_input_fingerprint
        output_match = original.output_fingerprint == replayed_output_fingerprint

        all_match = (
            engine_match
            and policy_match
            and reasoning_match
            and input_match
            and output_match
        )

        divergence = None
        if not all_match:
            mismatches = []
            if not engine_match:
                mismatches.append(
                    f"engine: {original.engine_version} vs {replayed_engine_version}"
                )
            if not policy_match:
                mismatches.append(
                    f"policy: {original.policy_version} vs {replayed_policy_version}"
                )
            if not reasoning_match:
                mismatches.append(
                    f"reasoning: {original.reasoning_version_id} vs {replayed_reasoning_version_id}"
                )
            if not input_match:
                mismatches.append(
                    f"input: {original.input_fingerprint} vs {replayed_input_fingerprint}"
                )
            if not output_match:
                mismatches.append(
                    f"output: {original.output_fingerprint} vs {replayed_output_fingerprint}"
                )
            divergence = "; ".join(mismatches)

        return ReplayValidationResult(
            matches=all_match,
            engine_version_match=engine_match,
            policy_version_match=policy_match,
            reasoning_version_match=reasoning_match,
            input_fingerprint_match=input_match,
            output_fingerprint_match=output_match,
            divergence_detail=divergence,
        )


class DeterminismViolationError(Exception):
    """Raised when replay determinism is violated."""

    def __init__(self, message: str, validation_result: ReplayValidationResult) -> None:
        super().__init__(message)
        self.validation_result = validation_result
