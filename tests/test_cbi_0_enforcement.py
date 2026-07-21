from __future__ import annotations

from dataclasses import dataclass

import pytest

from egregore.application.cbi_0_binding_audit_emitter import MemoryBindingAuditEmitter
from egregore.application.cbi_0_composition_guard import CompositionGuard
from egregore.application.cbi_0_projection_access_monitor import (
    ProjectionAccessMonitor,
)
from egregore.application.cbi_0_projection_registry_validator import (
    ProjectionRegistryValidator,
)
from egregore.domain.semantics.projection_descriptor import (
    IRField,
    OverlapClass,
    OverlapClassification,
    ProjectionConstraint,
    ProjectionDescriptor,
    SensitivityClass,
)
from egregore.interface.constraint_binding_ports import (
    CompositionGuardError,
    ProjectionBindingError,
    RegistryValidationError,
)
from egregore.shared.canonical import canonical_json

# -----------------------------
# Helpers
# -----------------------------

_READ_ONLY = ProjectionConstraint(name="read_only")
_EVIDENCE_BOUNDED = ProjectionConstraint(name="evidence_bounded")


def _mk_descriptor(
    *, agent_id: str, version: str, scope: set[IRField]
) -> ProjectionDescriptor:
    return ProjectionDescriptor(
        agent_id=agent_id,
        version=version,
        scope=frozenset(scope),
        constraints=frozenset({_READ_ONLY, _EVIDENCE_BOUNDED}),
        sensitivity_level=SensitivityClass.STANDARD,
    )


# -----------------------------
# M1 — IProjectionAccessMonitor
# -----------------------------


def test_m1_projection_access_monitor_allows_declared_scope() -> None:
    monitor = ProjectionAccessMonitor()
    desc = _mk_descriptor(
        agent_id="agent_a", version="v1", scope={IRField.ENTITY_TYPE, IRField.ATTRIBUTE}
    )

    monitor.declare(agent_id="agent_a", version="v1", descriptor=desc)

    monitor.validate_access(
        agent_id="agent_a",
        version="v1",
        accessed_fields=frozenset({IRField.ENTITY_TYPE}),
    )


def test_m1_projection_access_monitor_rejects_undeclared_field_access() -> None:
    monitor = ProjectionAccessMonitor()
    desc = _mk_descriptor(agent_id="agent_a", version="v1", scope={IRField.ENTITY_TYPE})

    monitor.declare(agent_id="agent_a", version="v1", descriptor=desc)

    with pytest.raises(ProjectionBindingError) as excinfo:
        monitor.validate_access(
            agent_id="agent_a",
            version="v1",
            accessed_fields=frozenset({IRField.ENTITY_TYPE, IRField.ATTRIBUTE}),
        )

    err = excinfo.value
    assert err.agent_id == "agent_a"
    assert err.version == "v1"
    assert IRField.ATTRIBUTE in err.undeclared_fields


# -----------------------------
# M2 — IProjectionRegistryValidator
# -----------------------------


def test_m2_validator_raises_when_active_descriptor_missing() -> None:
    validator = ProjectionRegistryValidator()

    with pytest.raises(RegistryValidationError):
        validator.validate_registry(
            descriptors={
                ("agent_a", "v1"): _mk_descriptor(
                    agent_id="agent_a", version="v1", scope={IRField.ENTITY_TYPE}
                )
            },
            overlap_classifications=[],
            active_agent_ids=[("agent_a", "v1"), ("agent_b", "v1")],
        )


def test_m2_validator_requires_overlap_classification_when_scopes_overlap() -> None:
    validator = ProjectionRegistryValidator()

    descriptors = {
        ("agent_a", "v1"): _mk_descriptor(
            agent_id="agent_a",
            version="v1",
            scope={IRField.ENTITY_TYPE, IRField.ATTRIBUTE},
        ),
        ("agent_b", "v1"): _mk_descriptor(
            agent_id="agent_b", version="v1", scope={IRField.ATTRIBUTE}
        ),
    }
    # overlap = {ATTRIBUTE} => non-empty => classification must exist
    with pytest.raises(RegistryValidationError, match="no overlap classification"):
        validator.validate_registry(
            descriptors=descriptors,
            overlap_classifications=[],
            active_agent_ids=[("agent_a", "v1"), ("agent_b", "v1")],
        )


def test_m2_validator_rejects_disjoint_classification_when_overlap_non_empty() -> None:
    validator = ProjectionRegistryValidator()

    descriptors = {
        ("agent_a", "v1"): _mk_descriptor(
            agent_id="agent_a",
            version="v1",
            scope={IRField.ENTITY_TYPE, IRField.ATTRIBUTE},
        ),
        ("agent_b", "v1"): _mk_descriptor(
            agent_id="agent_b", version="v1", scope={IRField.ATTRIBUTE}
        ),
    }
    overlap_cls = OverlapClassification(
        agent_id_a="agent_a",
        agent_id_b="agent_b",
        overlap_class=OverlapClass.DISJOINT,
    )

    with pytest.raises(RegistryValidationError, match="classification is DISJOINT"):
        validator.validate_registry(
            descriptors=descriptors,
            overlap_classifications=[overlap_cls],
            active_agent_ids=[("agent_a", "v1"), ("agent_b", "v1")],
        )


def test_m2_validator_accepts_equivalent_when_scopes_match() -> None:
    validator = ProjectionRegistryValidator()

    scope = {IRField.ENTITY_TYPE, IRField.EVIDENCE_BLOCK}
    descriptors = {
        ("agent_a", "v1"): _mk_descriptor(
            agent_id="agent_a", version="v1", scope=set(scope)
        ),
        ("agent_b", "v1"): _mk_descriptor(
            agent_id="agent_b", version="v1", scope=set(scope)
        ),
    }
    overlap_cls = OverlapClassification(
        agent_id_a="agent_a",
        agent_id_b="agent_b",
        overlap_class=OverlapClass.EQUIVALENT,
    )

    validator.validate_registry(
        descriptors=descriptors,
        overlap_classifications=[overlap_cls],
        active_agent_ids=[("agent_a", "v1"), ("agent_b", "v1")],
    )


# -----------------------------
# M3 — ICompositionGuard
# -----------------------------


@dataclass(frozen=True)
class TerminalObj:
    x: int


def test_m3_composition_guard_rejects_reentering_same_terminal_artifact() -> None:
    guard = CompositionGuard()
    terminal = TerminalObj(x=1)

    guard.assert_terminal(output=terminal, source_agent_id="agent_a")

    with pytest.raises(CompositionGuardError, match="Composition guard violation"):
        guard.assert_terminal(output=terminal, source_agent_id="agent_a")


def test_m3_composition_guard_rejects_implicit_ir_synthesis_by_target_type_name() -> (
    None
):
    guard = CompositionGuard()
    terminal = TerminalObj(x=7)

    # Mark terminal first; implicit synthesis checks are now keyed to known terminal artifacts.
    guard.assert_terminal(output=terminal, source_agent_id="agent_a")

    with pytest.raises(CompositionGuardError, match="Composition guard violation"):
        guard.assert_no_implicit_ir_synthesis(
            source_agent_id="agent_a",
            target_input=terminal,
            target_type_name="CanonicalSemanticIR",
        )


# -----------------------------
# M4 — IBindingAuditEmitter
# -----------------------------


def test_m4_emits_binding_audit_record_and_can_be_equivalent() -> None:
    emitter = MemoryBindingAuditEmitter()

    desc_a = _mk_descriptor(
        agent_id="agent_a", version="v1", scope={IRField.ENTITY_TYPE}
    )
    desc_b = _mk_descriptor(agent_id="agent_b", version="v1", scope={IRField.ATTRIBUTE})

    descriptors = {
        ("agent_a", "v1"): desc_a,
        ("agent_b", "v1"): desc_b,
    }

    # Mirror emitter’s registry_hash construction:
    items = sorted(
        (
            (agent_id, version, desc.canonical_hash())
            for (agent_id, version), desc in descriptors.items()
        ),
        key=lambda x: (x[0], x[1]),
    )
    registry_payload = {"descriptors": items}
    runtime_state_repr = canonical_json(registry_payload)

    record = emitter.emit_equivalence_sweep(
        descriptors=descriptors,
        runtime_state_repr=runtime_state_repr,
    )

    assert record.equivalence_status == "EQUIVALENT"
    assert record.binding_hook_id == "M4"
    assert len(emitter.emitted) == 1
    assert emitter.emitted[0] == record


def test_m4_emits_diverged_record_when_runtime_state_differs() -> None:
    emitter = MemoryBindingAuditEmitter()

    desc_a = _mk_descriptor(
        agent_id="agent_a", version="v1", scope={IRField.ENTITY_TYPE}
    )
    descriptors = {("agent_a", "v1"): desc_a}

    record = emitter.emit_equivalence_sweep(
        descriptors=descriptors,
        runtime_state_repr="not-the-registry-payload",
    )
    assert record.equivalence_status == "DIVERGED"
    assert len(emitter.emitted) == 1
