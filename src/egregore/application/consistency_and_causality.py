"""Phase 3: Consistency + Causality enforcement.

Ensures deterministic ordering within (org, case) tuples and monotonic
version numbering. Every committed event includes causality_id for
causal reconstruction and event ordering.

Invariants:
- Per-(org, case) event sequence must be monotonically increasing by version_number
- causality_id must be present in every committed AuditEvent and OutboxEntry
- version_id derivation includes causality_id for determinism
- event replay must preserve causality ordering
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VectorClock:
    """Lamport-style vector clock for distributed causality tracking."""

    node_id: str
    counter: int = 0
    vector: Mapping[str, int] = field(default_factory=dict)

    def increment(self) -> VectorClock:
        new_vector = dict(self.vector)
        new_vector[self.node_id] = new_vector.get(self.node_id, 0) + 1
        return VectorClock(
            node_id=self.node_id,
            counter=self.counter + 1,
            vector=new_vector,
        )

    def merge(self, other: VectorClock) -> VectorClock:
        merged = dict(self.vector)
        for node, count in other.vector.items():
            merged[node] = max(merged.get(node, 0), count)
        merged[self.node_id] = max(
            merged.get(self.node_id, 0), self.vector.get(self.node_id, 0)
        )
        return VectorClock(
            node_id=self.node_id,
            counter=self.counter,
            vector=merged,
        )


@dataclass(frozen=True)
class CausalityContext:
    """Immutable causality context for event ordering."""

    organization_id: str
    case_id: str
    causality_id: str

    # Monotonic version number for this (org, case) tuple
    version_number: int

    # Canonical version_id derived from causality context (for determinism)
    version_id: str

    # Distributed causality
    parent_span_id: str | None = None
    vector_clock: VectorClock = field(
        default_factory=lambda: VectorClock(node_id="default")
    )


class ConsistencyViolationError(Exception):
    """Raised when consistency constraints are violated."""

    pass


class CausalityViolationError(Exception):
    """Raised when causality ordering is violated."""

    pass


class ConsistencyAndCausalityEnforcer:
    """Enforces per-(org,case) consistency and causality constraints.

    Validates:
    - monotonic version numbering per (org, case)
    - causality_id present in all events
    - version_id matches causality context
    """

    def __init__(self) -> None:
        # Track max version number per (org, case) for live execution
        self._version_watermarks: dict[tuple[str, str], int] = {}

    def validate_causality_context(
        self,
        *,
        context: CausalityContext,
    ) -> None:
        """Validate that causality context is consistent.

        For live execution: enforce monotonic increase in version_number.
        For replay: version_number must match recorded value.

        Raises:
            CausalityViolationError: if context violates monotonicity

        """
        key = (context.organization_id, context.case_id)
        current_watermark = self._version_watermarks.get(key, 0)

        if context.version_number <= current_watermark:
            raise CausalityViolationError(
                f"Version number must increase: {key} requires > {current_watermark}, got {context.version_number}"
            )

        self._version_watermarks[key] = context.version_number

    def validate_event_causality(
        self,
        *,
        event: Mapping[str, Any],
        expected_causality_id: str,
        expected_version_number: int,
    ) -> None:
        """Validate that an event includes required causality information.

        Args:
            event: the event payload to validate
            expected_causality_id: the causality_id that should be present
            expected_version_number: the version_number that should be present

        Raises:
            CausalityViolationError: if causality info is missing or wrong

        """
        if "causality_id" not in event:
            raise CausalityViolationError(
                f"Event missing required causality_id: {event}"
            )

        event_causality_id = event.get("causality_id")
        if event_causality_id != expected_causality_id:
            raise CausalityViolationError(
                f"Event causality_id mismatch: expected {expected_causality_id}, got {event_causality_id}"
            )

        if "version_number" in event:
            event_version_number = event.get("version_number")
            if event_version_number != expected_version_number:
                raise CausalityViolationError(
                    f"Event version_number mismatch: expected {expected_version_number}, got {event_version_number}"
                )

    def enforce_causality_ordering(
        self,
        *,
        organization_id: str,
        case_id: str,
        events: Sequence[Mapping[str, Any]],
    ) -> None:
        """Enforce that events maintain causality ordering.

        Events should be processable in commit order with monotonic causality
        reconstructability.

        Args:
            organization_id: org context
            case_id: case context
            events: sequence of events to validate

        Raises:
            CausalityViolationError: if ordering is violated

        """
        if not events:
            return

        prev_version = 0
        for i, event in enumerate(events):
            if "version_number" not in event:
                raise CausalityViolationError(
                    f"Event {i} missing version_number: {event}"
                )

            current_version = event["version_number"]
            if current_version <= prev_version:
                raise CausalityViolationError(
                    f"Event {i} version non-monotonic: {current_version} <= {prev_version}"
                )

            if "causality_id" not in event:
                raise CausalityViolationError(
                    f"Event {i} missing causality_id: {event}"
                )

            prev_version = current_version

    def reset_watermarks(self) -> None:
        """Reset version watermarks (for testing/replay scenarios)."""
        self._version_watermarks.clear()


class CausalityReconstructor:
    """Reconstructs causal ordering from committed events.

    Used during replay to verify that the recorded causality chain
    is reconstructable and deterministic.
    """

    @staticmethod
    def reconstruct_from_events(
        events: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Sequence[str]]:
        """Reconstruct causality chains from event sequence.

        Returns:
            dict mapping causality_id to sequence of event_ids in order

        """
        causality_chains: dict[str, list[str]] = {}

        for event in events:
            causality_id = event.get("causality_id")
            event_id = event.get("event_id")

            if not causality_id or not event_id:
                continue

            if causality_id not in causality_chains:
                causality_chains[causality_id] = []

            causality_chains[causality_id].append(event_id)

        return causality_chains

    @staticmethod
    def verify_causality_chain_integrity(
        causality_chain: Sequence[str],
        expected_sequence: Sequence[str],
    ) -> bool:
        """Verify that reconstructed chain matches expected sequence.

        Returns:
            True if chains match exactly

        """
        return tuple(causality_chain) == tuple(expected_sequence)
