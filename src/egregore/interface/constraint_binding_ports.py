"""CBI-0 Constraint Binding Interface — port contracts (Gate 4.3).

Defines the four enforcement surfaces of CBI-0. Each Protocol corresponds to one
binding hook in the spec (gate_4_3_constraint_binding_interface.md).

Authority note: these interfaces bind existing constraints. They do not define
new semantic rules. Do not add methods that classify IR fields (Gate 4.1/4.2 scope),
define agent types, or implement arbitration policies.

Hook map:
    IProjectionAccessMonitor   → spec M1 (projection scope enforcement)
    IProjectionRegistryValidator → spec M2 (registry completeness and consistency)
    ICompositionGuard          → spec M3 (terminal output non-re-entry)
    IBindingAuditEmitter       → spec M4 (spec/runtime equivalence auditability)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from egregore.domain.legal_agent.errors import RegistryValidationError  # noqa: F401
from egregore.domain.semantics.projection_descriptor import (
    BindingAuditRecord,
    IRField,
    OverlapClassification,
    ProjectionDescriptor,
)

# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class ProjectionBindingError(Exception):
    """Raised by IProjectionAccessMonitor when M1 is violated.

    Violation: an agent accessed an IR field outside its declared projection scope.
    This is a fail-closed structural error. No partial output is permitted.
    """

    def __init__(
        self,
        agent_id: str,
        version: str,
        undeclared_fields: frozenset[IRField],
        declared_scope: frozenset[IRField],
    ) -> None:
        self.agent_id = agent_id
        self.version = version
        self.undeclared_fields = undeclared_fields
        self.declared_scope = declared_scope
        super().__init__(
            f"Agent {agent_id!r} v{version} accessed IR fields outside declared scope. "
            f"Undeclared: {sorted(f.value for f in undeclared_fields)}. "
            f"Declared: {sorted(f.value for f in declared_scope)}."
        )


class CompositionGuardError(Exception):
    """Raised by ICompositionGuard when M3 is violated.

    Violation: a terminal agent output is being treated as or routed into canonical IR
    without an explicit re-validation bridge.
    This is a fail-closed structural error.
    """

    def __init__(
        self, source_agent_id: str, output_type: str, target_type: str
    ) -> None:
        self.source_agent_id = source_agent_id
        self.output_type = output_type
        self.target_type = target_type
        super().__init__(
            f"Composition guard violation: output of type {output_type!r} from agent "
            f"{source_agent_id!r} cannot enter {target_type!r} without re-validation bridge."
        )


# ---------------------------------------------------------------------------
# Enforcement surface protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class IProjectionAccessMonitor(Protocol):
    """CBI-0 M1: per-execution projection scope enforcement.

    Called once per agent execution to verify that the set of IR fields actually
    accessed is a subset of the agent's declared projection scope.

    Implementations MUST be fail-closed: if the set check fails, raise
    ProjectionBindingError immediately. No partial execution result is permitted.
    """

    def declare(
        self,
        agent_id: str,
        version: str,
        descriptor: ProjectionDescriptor,
    ) -> None:
        """Register a descriptor before execution begins.

        Called once per agent at admission time (not per-execution). Implementations
        may cache the descriptor for use in validate_access().

        Raises:
            RegistryValidationError: if descriptor is internally invalid.

        """
        ...

    def validate_access(
        self,
        agent_id: str,
        version: str,
        accessed_fields: frozenset[IRField],
    ) -> None:
        """Check that accessed_fields ⊆ declared_projection(agent_id, version).

        Raises:
            ProjectionBindingError: immediately on any field outside declared scope.
            RegistryValidationError: if no descriptor has been declared for this
                agent/version pair.

        """
        ...


@runtime_checkable
class IProjectionRegistryValidator(Protocol):
    """CBI-0 M2: startup/pre-execution registry completeness and consistency.

    Must be invoked before any multi-agent orchestration run begins.
    Single-agent baseline: descriptor presence and scope integrity still required;
    pairwise overlap classification is vacuously satisfied.
    """

    def validate_registry(
        self,
        descriptors: dict[tuple[str, str], ProjectionDescriptor],
        overlap_classifications: list[OverlapClassification],
        active_agent_ids: list[tuple[str, str]],
    ) -> None:
        """Validate registry completeness and pairwise overlap consistency.

        Args:
            descriptors: mapping of (agent_id, version) → ProjectionDescriptor.
                All currently registered descriptors.
            overlap_classifications: all declared pairwise overlap classifications.
            active_agent_ids: (agent_id, version) pairs expected in the current
                orchestration run. Every pair must have a descriptor in `descriptors`.

        Raises:
            RegistryValidationError: on any of:
                - missing descriptor for any active agent
                - missing overlap classification for any active pair with non-disjoint scope
                - overlap classification inconsistent with computed scope intersection

        """
        ...


@runtime_checkable
class ICompositionGuard(Protocol):
    """CBI-0 M3: terminal output non-re-entry guard.

    Active even in single-agent mode. Prevents terminal agent outputs from silently
    re-entering canonical IR space.

    'Canonical IR space' means: any object treated as or passed as a CanonicalSemanticIR
    to BIOK validation, executor, or another agent's input pathway.
    """

    def assert_terminal(
        self,
        output: Any,
        source_agent_id: str,
    ) -> None:
        """Verify that `output` is not already marked as terminal from a prior agent.

        Called on the output produced by source_agent_id to confirm it is a fresh
        terminal artifact (not a recycled prior output being re-used as input).

        Raises:
            CompositionGuardError: if output is already a registered terminal artifact
                and is being re-emitted without a re-validation bridge.

        """
        ...

    def assert_no_implicit_ir_synthesis(
        self,
        source_agent_id: str,
        target_input: Any,
        target_type_name: str,
    ) -> None:
        """Verify that target_input does not silently become CanonicalSemanticIR.

        Called at orchestration boundaries before routing any agent output onward.

        Args:
            source_agent_id: the agent that produced target_input.
            target_input: the object being routed.
            target_type_name: the name of the type it would be routed into.

        Raises:
            CompositionGuardError: if target_type_name is a canonical IR type and
                target_input has not passed through a registered re-validation bridge.

        """
        ...


@runtime_checkable
class IBindingAuditEmitter(Protocol):
    """CBI-0 M4: spec/runtime equivalence auditability.

    Produces structured BindingAuditRecord objects for every M1–M3 enforcement event
    and on-demand for M4 equivalence sweeps.

    Implementations MUST NOT suppress records. Every enforcement failure must emit.
    """

    def emit(self, record: BindingAuditRecord) -> None:
        """Emit a structured audit record.

        Called whenever a binding hook fires (violation or audit sweep).
        The record contains enough information for post-incident analysis.

        Implementations may log, write to a ledger, or accumulate in-memory.
        The interface makes no guarantee about durability — durability is a
        deployment concern, not an interface requirement.
        """
        ...

    def emit_equivalence_sweep(
        self,
        descriptors: dict[tuple[str, str], ProjectionDescriptor],
        runtime_state_repr: str,
    ) -> BindingAuditRecord:
        """Perform a spec/runtime equivalence check and emit the result.

        Args:
            descriptors: current registry state as a dict of descriptors.
            runtime_state_repr: a serialized/hashable representation of observed
                runtime state (what agents have actually accessed).

        Returns:
            BindingAuditRecord with equivalence_status "EQUIVALENT" or "DIVERGED".
            The record is also emitted via emit().

        """
        ...
