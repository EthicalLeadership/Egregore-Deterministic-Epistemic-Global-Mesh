"""Tests for Phase 2: Policy Versioning and Replay Determinism.

Validates that policy execution is deterministic, version-pinned,
and faithfully replayable using the exact versions from the original execution.
"""

from collections.abc import Mapping
from typing import Any

import pytest

from egregore.application.policy_versioning import (
    InMemoryPolicyVersionRegistry,
    VersionedPolicyExecutor,
)
from egregore.application.replay_determinism import (
    ReplayDeterminismValidator,
)


class StrictPolicy:
    """Test policy that rejects certain inputs."""

    def __init__(self, reject_key: str = "forbidden") -> None:
        self.reject_key = reject_key

    def validate(self, command: Any) -> None:
        if isinstance(command, dict) and self.reject_key in command:
            raise ValueError(f"Policy rejects commands with key: {self.reject_key}")

    def compute(self, command: Any) -> Mapping[str, Any]:
        if not isinstance(command, dict):
            return {"error": "command must be dict"}
        return {
            "policy_applied": True,
            "policy_decision": "APPROVED",
            "input_keys": list(command.keys()),
        }


def test_policy_versioning_executor_applies_versioned_policy():
    """Validate that VersionedPolicyExecutor uses exact policy version."""
    v1_policy = StrictPolicy(reject_key="v1_forbidden")
    v2_policy = StrictPolicy(reject_key="v2_forbidden")

    registry = InMemoryPolicyVersionRegistry()
    registry.register("v1.0", v1_policy)
    registry.register("v2.0", v2_policy)

    executor = VersionedPolicyExecutor(registry=registry)

    command = {"data": "test"}

    # Execute with v1 policy
    result_v1 = executor.execute(
        command=command,
        engine_version="engine-1.0",
        policy_version="v1.0",
    )

    assert result_v1.policy_version == "v1.0"
    assert result_v1.engine_version == "engine-1.0"
    assert result_v1.policy_result["policy_applied"] is True

    # Execute with v2 policy
    result_v2 = executor.execute(
        command=command,
        engine_version="engine-1.0",
        policy_version="v2.0",
    )

    assert result_v2.policy_version == "v2.0"
    assert result_v2.policy_result == result_v1.policy_result  # compute logic same


def test_policy_versioning_rejects_unknown_version():
    """Policy lookup must fail for unknown versions."""
    registry = InMemoryPolicyVersionRegistry()
    registry.register("v1.0", StrictPolicy())

    executor = VersionedPolicyExecutor(registry=registry)

    with pytest.raises(ValueError, match="Policy version not found"):
        executor.execute(
            command={},
            engine_version="engine-1.0",
            policy_version="v99.0",
        )


def test_policy_validation_enforced_before_compute():
    """Policy.validate() must reject invalid inputs before compute."""
    policy = StrictPolicy(reject_key="bad")
    registry = InMemoryPolicyVersionRegistry()
    registry.register("v1.0", policy)

    executor = VersionedPolicyExecutor(registry=registry)

    with pytest.raises(ValueError, match="Policy rejects"):
        executor.execute(
            command={"bad": "data"},
            engine_version="engine-1.0",
            policy_version="v1.0",
        )


def test_replay_determinism_metadata_captures_versions():
    """ReplayDeterminismMetadata must capture all version info for replay."""
    validator = ReplayDeterminismValidator()

    metadata = validator.record_execution(
        engine_version="engine-1.0",
        policy_version="policy-v1",
        reasoning_version_id="reasoning-v1",
        input_fingerprint="input-hash-abc123",
        output_fingerprint="output-hash-xyz789",
    )

    assert metadata.engine_version == "engine-1.0"
    assert metadata.policy_version == "policy-v1"
    assert metadata.reasoning_version_id == "reasoning-v1"
    assert metadata.input_fingerprint == "input-hash-abc123"
    assert metadata.output_fingerprint == "output-hash-xyz789"


def test_replay_validation_matches_identical_versions_and_fingerprints():
    """Replay validation must pass when all versions and fingerprints match."""
    validator = ReplayDeterminismValidator()

    metadata = validator.record_execution(
        engine_version="engine-1.0",
        policy_version="policy-v1",
        reasoning_version_id="reasoning-v1",
        input_fingerprint="input-hash-abc123",
        output_fingerprint="output-hash-xyz789",
    )

    # Replay with identical versions
    result = validator.validate_replay(
        original=metadata,
        replayed_engine_version="engine-1.0",
        replayed_policy_version="policy-v1",
        replayed_reasoning_version_id="reasoning-v1",
        replayed_input_fingerprint="input-hash-abc123",
        replayed_output_fingerprint="output-hash-xyz789",
    )

    assert result.matches is True
    assert result.engine_version_match is True
    assert result.policy_version_match is True
    assert result.reasoning_version_match is True
    assert result.input_fingerprint_match is True
    assert result.output_fingerprint_match is True
    assert result.divergence_detail is None


def test_replay_validation_detects_version_mismatch():
    """Replay validation must detect version mismatches."""
    validator = ReplayDeterminismValidator()

    metadata = validator.record_execution(
        engine_version="engine-1.0",
        policy_version="policy-v1",
        reasoning_version_id="reasoning-v1",
        input_fingerprint="input-hash-abc123",
        output_fingerprint="output-hash-xyz789",
    )

    # Replay with different policy version
    result = validator.validate_replay(
        original=metadata,
        replayed_engine_version="engine-1.0",
        replayed_policy_version="policy-v2",  # MISMATCH
        replayed_reasoning_version_id="reasoning-v1",
        replayed_input_fingerprint="input-hash-abc123",
        replayed_output_fingerprint="output-hash-xyz789",
    )

    assert result.matches is False
    assert result.policy_version_match is False
    assert "policy-v1 vs policy-v2" in result.divergence_detail


def test_replay_validation_detects_output_fingerprint_mismatch():
    """Replay validation must detect output divergence."""
    validator = ReplayDeterminismValidator()

    metadata = validator.record_execution(
        engine_version="engine-1.0",
        policy_version="policy-v1",
        reasoning_version_id="reasoning-v1",
        input_fingerprint="input-hash-abc123",
        output_fingerprint="output-hash-original",
    )

    # Replay with different output
    result = validator.validate_replay(
        original=metadata,
        replayed_engine_version="engine-1.0",
        replayed_policy_version="policy-v1",
        replayed_reasoning_version_id="reasoning-v1",
        replayed_input_fingerprint="input-hash-abc123",
        replayed_output_fingerprint="output-hash-diverged",  # MISMATCH
    )

    assert result.matches is False
    assert result.output_fingerprint_match is False
    assert "output-hash-original vs output-hash-diverged" in result.divergence_detail


def test_replay_validation_detects_multiple_mismatches():
    """Replay validation must report all divergences."""
    validator = ReplayDeterminismValidator()

    metadata = validator.record_execution(
        engine_version="engine-1.0",
        policy_version="policy-v1",
        reasoning_version_id="reasoning-v1",
        input_fingerprint="input-v1",
        output_fingerprint="output-v1",
    )

    # Replay with multiple mismatches
    result = validator.validate_replay(
        original=metadata,
        replayed_engine_version="engine-2.0",  # MISMATCH
        replayed_policy_version="policy-v2",  # MISMATCH
        replayed_reasoning_version_id="reasoning-v1",
        replayed_input_fingerprint="input-v2",  # MISMATCH
        replayed_output_fingerprint="output-v2",  # MISMATCH
    )

    assert result.matches is False
    assert result.engine_version_match is False
    assert result.policy_version_match is False
    assert result.input_fingerprint_match is False
    assert result.output_fingerprint_match is False
    assert "engine:" in result.divergence_detail
    assert "policy:" in result.divergence_detail
    assert "input:" in result.divergence_detail
    assert "output:" in result.divergence_detail


def test_in_memory_registry_register_and_lookup():
    """InMemoryPolicyVersionRegistry must support register and lookup."""
    policy_v1 = StrictPolicy()
    policy_v2 = StrictPolicy()

    registry = InMemoryPolicyVersionRegistry()
    registry.register("v1.0", policy_v1)
    registry.register("v2.0", policy_v2)

    retrieved_v1 = registry.lookup("v1.0")
    retrieved_v2 = registry.lookup("v2.0")

    assert retrieved_v1 is policy_v1
    assert retrieved_v2 is policy_v2


def test_in_memory_registry_set_current_version():
    """InMemoryPolicyVersionRegistry must track current version."""
    policy = StrictPolicy()
    registry = InMemoryPolicyVersionRegistry()
    registry.register("v1.0", policy)
    registry.register("v2.0", policy)

    assert registry.current_version() == "v1.0.0"  # Default

    registry.set_current("v2.0")
    assert registry.current_version() == "v2.0"


def test_in_memory_registry_rejects_unknown_set_current():
    """InMemoryPolicyVersionRegistry must reject setting unknown version as current."""
    registry = InMemoryPolicyVersionRegistry()
    registry.register("v1.0", StrictPolicy())

    with pytest.raises(ValueError, match="Cannot set current to unknown version"):
        registry.set_current("v99.0")
