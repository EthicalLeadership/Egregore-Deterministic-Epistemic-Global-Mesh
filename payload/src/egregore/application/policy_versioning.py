"""Policy versioning and replay determinism for Phase 2.

Ensures that policy execution is deterministic and version-pinned for replay.
Each command execution includes engine_version and policy_version that allow
faithful reconstruction of the exact policy that was applied.

Invariants:
- policy_version must be explicitly provided (no automatic version detection)
- once a policy_version is committed, it must be immutable for replay
- version_id derivation must include both engine_version and policy_version
- replay must use the same versions from the original commit
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class IPolicyVersionRegistry(Protocol):
    """Port: provides access to versioned policy logic.

    Implementations must be deterministic:
    - lookup(version) must return identical results across calls
    - no external state dependency
    - no timestamp-based decisions
    """

    def lookup(self, policy_version: str) -> IPolicyLogic:
        """Retrieve policy logic for the given version.

        Raises:
            ValueError: if version not found or is malformed

        """
        ...

    def current_version(self) -> str:
        """Get the current active policy version.

        Used only for new commands; replay always uses explicit versions.
        """
        ...


class IPolicyLogic(Protocol):
    """Protocol: deterministic policy evaluation."""

    def validate(self, command: Any) -> None:
        """Validate command against policy constraints.

        Raises:
            ValueError: if validation fails

        """
        ...

    def compute(self, command: Any) -> Mapping[str, Any]:
        """Execute deterministic policy logic.

        Must be pure: same inputs always produce identical outputs.
        No side effects, no external I/O.
        """
        ...


@dataclass(frozen=True)
class VersionedPolicyExecution:
    """Result of deterministic policy execution with versioning metadata.

    Captures the exact policy applied so replay can faithfully recreate it.
    """

    policy_version: str
    engine_version: str
    policy_result: Mapping[str, Any]

    # For replay: record which policy rules were evaluated
    applied_rules: tuple[str, ...] = ()

    # Deterministic hash of the policy decision chain (for audit)
    decision_trace_hash: str | None = None


class VersionedPolicyExecutor:
    """Deterministic policy executor with version pinning.

    Ensures that every policy application is tied to exact versions,
    making replay faithful to the original decision logic.
    """

    def __init__(
        self,
        *,
        registry: IPolicyVersionRegistry,
        policy_decision_tracer: Callable[[str, Mapping[str, Any]], str] | None = None,
    ) -> None:
        self._registry = registry
        self._tracer = policy_decision_tracer

    def execute(
        self,
        *,
        command: Any,
        engine_version: str,
        policy_version: str,
    ) -> VersionedPolicyExecution:
        """Execute policy with version pinning.

        Args:
            command: the command to validate/execute against
            engine_version: engine version for version_id derivation
            policy_version: exact policy version to use

        Returns:
            VersionedPolicyExecution with versioning metadata

        Raises:
            ValueError: if policy_version not found or execution fails

        """
        # Lookup versioned policy (deterministic)
        policy = self._registry.lookup(policy_version)

        # Validate command against policy
        policy.validate(command)

        # Execute policy (must be pure)
        policy_result = policy.compute(command)

        # Compute decision trace if tracer provided
        decision_trace_hash = None
        applied_rules = ()
        if self._tracer:
            decision_trace_hash = self._tracer(policy_version, policy_result)

        return VersionedPolicyExecution(
            policy_version=policy_version,
            engine_version=engine_version,
            policy_result=policy_result,
            applied_rules=applied_rules,
            decision_trace_hash=decision_trace_hash,
        )


class InMemoryPolicyVersionRegistry:
    """Simple in-memory policy registry for testing and Phase 0.

    Maps policy version strings to callable policy logic.
    """

    def __init__(self, policies: Mapping[str, IPolicyLogic] | None = None) -> None:
        self._policies = dict(policies) if policies else {}
        self._current = "v1.0.0"

    def lookup(self, version: str) -> IPolicyLogic:
        if version not in self._policies:
            raise ValueError(f"Policy version not found: {version}")
        return self._policies[version]

    def current_version(self) -> str:
        return self._current

    def register(self, version: str, policy: IPolicyLogic) -> None:
        """Register a new policy version."""
        self._policies[version] = policy

    def set_current(self, version: str) -> None:
        """Set the current active policy version."""
        if version not in self._policies:
            raise ValueError(f"Cannot set current to unknown version: {version}")
        self._current = version
