"""Tests for CBI-0 Constraint Binding Interface — Gate 4.3.

Test categories:
1. Projection descriptor validity (data types, hashing, constraints)
2. Legal Agent v1 descriptor accuracy (grounded against empirical IR observation)
3. StaticProjectionRegistry behavior (lookup, scope enforcement, overlap)
4. Composition guard boundary (terminal output types vs canonical IR types)
5. Error type structure (correct attributes, fail-closed messages)
6. Port protocol conformance (runtime_checkable Protocol checks)
"""

from __future__ import annotations

from typing import Any

import pytest

from egregore.domain.legal_agent.projection_registry import (
    LEGAL_AGENT_V1_DESCRIPTOR,
    StaticProjectionRegistry,
)
from egregore.domain.semantics.projection_descriptor import (
    BindingAuditRecord,
    IRField,
    OverlapClass,
    OverlapClassification,
    ProjectionConstraint,
    ProjectionDescriptor,
    SensitivityClass,
)
from egregore.interface.constraint_binding_ports import (
    CompositionGuardError,
    IBindingAuditEmitter,
    ICompositionGuard,
    IProjectionAccessMonitor,
    IProjectionRegistryValidator,
    ProjectionBindingError,
    RegistryValidationError,
)

# ---------------------------------------------------------------------------
# 1. Projection descriptor validity
# ---------------------------------------------------------------------------


class TestIRFieldEnum:
    def test_all_six_canonical_fields_defined(self) -> None:
        expected = {
            "entity_type",
            "relation",
            "attribute",
            "evidence_block",
            "inference_node",
            "metadata_block",
        }
        actual = {f.value for f in IRField}
        assert actual == expected

    def test_ir_field_is_string_enum(self) -> None:
        for f in IRField:
            assert isinstance(f, str), f"IRField.{f.name} is not a str subclass"

    def test_ir_field_values_are_stable(self) -> None:
        # Names must never change once deployed — they are registry keys.
        assert IRField.ENTITY_TYPE.value == "entity_type"
        assert IRField.RELATION.value == "relation"
        assert IRField.ATTRIBUTE.value == "attribute"
        assert IRField.EVIDENCE_BLOCK.value == "evidence_block"
        assert IRField.INFERENCE_NODE.value == "inference_node"
        assert IRField.METADATA_BLOCK.value == "metadata_block"


class TestSensitivityClassEnum:
    def test_three_levels_defined(self) -> None:
        assert {c.value for c in SensitivityClass} == {
            "standard",
            "restricted",
            "sensitive",
        }


class TestOverlapClassEnum:
    def test_five_classes_defined(self) -> None:
        expected = {
            "disjoint",
            "equivalent",
            "dependent",
            "interference_prone",
            "conflict_sensitive",
        }
        assert {c.value for c in OverlapClass} == expected


class TestProjectionDescriptor:
    def _make(
        self,
        agent_id: str = "test_agent",
        version: str = "v1.0",
        scope: frozenset[IRField] | None = None,
        constraints: frozenset[ProjectionConstraint] | None = None,
        sensitivity_level: SensitivityClass = SensitivityClass.STANDARD,
    ) -> ProjectionDescriptor:
        return ProjectionDescriptor(
            agent_id=agent_id,
            version=version,
            scope=scope if scope is not None else frozenset({IRField.ATTRIBUTE}),
            constraints=constraints if constraints is not None else frozenset(),
            sensitivity_level=sensitivity_level,
        )

    def test_descriptor_is_frozen(self) -> None:
        d = self._make()
        with pytest.raises((AttributeError, TypeError)):
            d.agent_id = "mutated"  # type: ignore[misc]

    def test_canonical_hash_is_deterministic(self) -> None:
        d = self._make()
        assert d.canonical_hash() == d.canonical_hash()

    def test_canonical_hash_is_sha256_hex(self) -> None:
        d = self._make()
        h = d.canonical_hash()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_scopes_produce_different_hashes(self) -> None:
        d1 = self._make(scope=frozenset({IRField.ATTRIBUTE}))
        d2 = self._make(scope=frozenset({IRField.ATTRIBUTE, IRField.EVIDENCE_BLOCK}))
        assert d1.canonical_hash() != d2.canonical_hash()

    def test_hash_is_order_independent_over_scope(self) -> None:
        # frozenset construction order should not affect hash.
        d1 = self._make(scope=frozenset({IRField.ATTRIBUTE, IRField.ENTITY_TYPE}))
        d2 = self._make(scope=frozenset({IRField.ENTITY_TYPE, IRField.ATTRIBUTE}))
        assert d1.canonical_hash() == d2.canonical_hash()

    def test_descriptor_equality_by_value(self) -> None:
        d1 = self._make()
        d2 = self._make()
        assert d1 == d2

    def test_descriptor_scope_is_frozenset(self) -> None:
        d = self._make()
        assert isinstance(d.scope, frozenset)

    def test_descriptor_constraints_is_frozenset(self) -> None:
        d = self._make()
        assert isinstance(d.constraints, frozenset)


class TestProjectionConstraint:
    def test_constraint_is_frozen(self) -> None:
        c = ProjectionConstraint(name="read_only")
        with pytest.raises((AttributeError, TypeError)):
            c.name = "mutated"  # type: ignore[misc]

    def test_constraint_equality_by_name(self) -> None:
        c1 = ProjectionConstraint(name="read_only")
        c2 = ProjectionConstraint(name="read_only")
        assert c1 == c2


class TestOverlapClassification:
    def test_conflict_sensitive_requires_arbitration_ref(self) -> None:
        with pytest.raises(ValueError, match="arbitration_policy_ref"):
            OverlapClassification(
                agent_id_a="agent_a",
                agent_id_b="agent_b",
                overlap_class=OverlapClass.CONFLICT_SENSITIVE,
                arbitration_policy_ref="",
            )

    def test_conflict_sensitive_accepts_non_empty_ref(self) -> None:
        c = OverlapClassification(
            agent_id_a="agent_a",
            agent_id_b="agent_b",
            overlap_class=OverlapClass.CONFLICT_SENSITIVE,
            arbitration_policy_ref="policy://conflict-v1",
        )
        assert c.arbitration_policy_ref == "policy://conflict-v1"

    def test_disjoint_accepts_empty_ref(self) -> None:
        c = OverlapClassification(
            agent_id_a="agent_a",
            agent_id_b="agent_b",
            overlap_class=OverlapClass.DISJOINT,
        )
        assert c.arbitration_policy_ref == ""

    def test_is_frozen(self) -> None:
        c = OverlapClassification(
            agent_id_a="a",
            agent_id_b="b",
            overlap_class=OverlapClass.DISJOINT,
        )
        with pytest.raises((AttributeError, TypeError)):
            c.overlap_class = OverlapClass.EQUIVALENT  # type: ignore[misc]


class TestBindingAuditRecord:
    def test_is_frozen(self) -> None:
        r = BindingAuditRecord(
            registry_hash="abc",
            runtime_state_hash="def",
            equivalence_status="EQUIVALENT",
            divergence_details="",
            agent_id="legal_agent",
            binding_hook_id="M1",
        )
        with pytest.raises((AttributeError, TypeError)):
            r.equivalence_status = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Legal Agent v1 descriptor accuracy
# ---------------------------------------------------------------------------


class TestLegalAgentV1Descriptor:
    def test_agent_id_is_legal_agent(self) -> None:
        assert LEGAL_AGENT_V1_DESCRIPTOR.agent_id == "legal_agent"

    def test_version_is_v1_0(self) -> None:
        assert LEGAL_AGENT_V1_DESCRIPTOR.version == "v1.0"

    def test_scope_contains_entity_type(self) -> None:
        assert IRField.ENTITY_TYPE in LEGAL_AGENT_V1_DESCRIPTOR.scope

    def test_scope_contains_attribute(self) -> None:
        assert IRField.ATTRIBUTE in LEGAL_AGENT_V1_DESCRIPTOR.scope

    def test_scope_contains_evidence_block(self) -> None:
        assert IRField.EVIDENCE_BLOCK in LEGAL_AGENT_V1_DESCRIPTOR.scope

    def test_scope_excludes_relation(self) -> None:
        assert IRField.RELATION not in LEGAL_AGENT_V1_DESCRIPTOR.scope

    def test_scope_excludes_inference_node(self) -> None:
        # Legal Agent v1 does not consume prior inference nodes from IR input
        assert IRField.INFERENCE_NODE not in LEGAL_AGENT_V1_DESCRIPTOR.scope

    def test_scope_excludes_metadata_block(self) -> None:
        # version_id and reasoning_version_id are executor metadata, not agent scope
        assert IRField.METADATA_BLOCK not in LEGAL_AGENT_V1_DESCRIPTOR.scope

    def test_scope_size_is_exactly_three(self) -> None:
        assert len(LEGAL_AGENT_V1_DESCRIPTOR.scope) == 3

    def test_sensitivity_level_is_standard(self) -> None:
        assert LEGAL_AGENT_V1_DESCRIPTOR.sensitivity_level == SensitivityClass.STANDARD

    def test_read_only_constraint_present(self) -> None:
        names = {c.name for c in LEGAL_AGENT_V1_DESCRIPTOR.constraints}
        assert "read_only" in names

    def test_evidence_bounded_constraint_present(self) -> None:
        names = {c.name for c in LEGAL_AGENT_V1_DESCRIPTOR.constraints}
        assert "evidence_bounded" in names

    def test_no_derived_output_constraint_present(self) -> None:
        names = {c.name for c in LEGAL_AGENT_V1_DESCRIPTOR.constraints}
        assert "no_derived_output" in names

    def test_descriptor_is_frozen(self) -> None:
        with pytest.raises((AttributeError, TypeError)):
            LEGAL_AGENT_V1_DESCRIPTOR.agent_id = "tampered"  # type: ignore[misc]

    def test_canonical_hash_is_stable(self) -> None:
        h1 = LEGAL_AGENT_V1_DESCRIPTOR.canonical_hash()
        h2 = LEGAL_AGENT_V1_DESCRIPTOR.canonical_hash()
        assert h1 == h2
        assert len(h1) == 64


# ---------------------------------------------------------------------------
# 3. StaticProjectionRegistry
# ---------------------------------------------------------------------------


class TestStaticProjectionRegistry:
    def setup_method(self) -> None:
        self.registry = StaticProjectionRegistry()

    def test_get_legal_agent_v1_succeeds(self) -> None:
        d = self.registry.get("legal_agent", "v1.0")
        assert d == LEGAL_AGENT_V1_DESCRIPTOR

    def test_get_missing_agent_raises_registry_validation_error(self) -> None:
        with pytest.raises(RegistryValidationError):
            self.registry.get("unknown_agent", "v1.0")

    def test_get_wrong_version_raises_registry_validation_error(self) -> None:
        with pytest.raises(RegistryValidationError):
            self.registry.get("legal_agent", "v9.0")

    def test_all_descriptors_contains_legal_agent_v1(self) -> None:
        all_d = self.registry.all_descriptors()
        assert ("legal_agent", "v1.0") in all_d

    def test_all_descriptors_is_a_copy(self) -> None:
        d1 = self.registry.all_descriptors()
        d2 = self.registry.all_descriptors()
        assert d1 is not d2

    def test_all_overlap_classifications_is_empty_in_single_agent_mode(self) -> None:
        assert self.registry.all_overlap_classifications() == []

    def test_validate_agent_scope_within_declared_passes(self) -> None:
        # All three declared fields — should not raise.
        self.registry.validate_agent_scope(
            "legal_agent",
            "v1.0",
            frozenset({IRField.ENTITY_TYPE, IRField.ATTRIBUTE, IRField.EVIDENCE_BLOCK}),
        )

    def test_validate_agent_scope_subset_of_declared_passes(self) -> None:
        self.registry.validate_agent_scope(
            "legal_agent",
            "v1.0",
            frozenset({IRField.ATTRIBUTE}),
        )

    def test_validate_agent_scope_empty_set_passes(self) -> None:
        self.registry.validate_agent_scope("legal_agent", "v1.0", frozenset())

    def test_validate_agent_scope_outside_declared_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="undeclared IR fields"):
            self.registry.validate_agent_scope(
                "legal_agent",
                "v1.0",
                frozenset({IRField.ATTRIBUTE, IRField.METADATA_BLOCK}),
            )

    def test_validate_agent_scope_relation_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            self.registry.validate_agent_scope(
                "legal_agent",
                "v1.0",
                frozenset({IRField.RELATION}),
            )

    def test_validate_agent_scope_inference_node_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            self.registry.validate_agent_scope(
                "legal_agent",
                "v1.0",
                frozenset({IRField.INFERENCE_NODE}),
            )

    def test_validate_agent_scope_unknown_agent_raises_registry_validation_error(
        self,
    ) -> None:
        with pytest.raises(RegistryValidationError):
            self.registry.validate_agent_scope("ghost_agent", "v1.0", frozenset())

    def test_validate_pairwise_overlap_single_agent_vacuously_passes(self) -> None:
        # No raise — vacuously satisfied with one agent.
        self.registry.validate_pairwise_overlap([("legal_agent", "v1.0")])

    def test_validate_pairwise_overlap_empty_list_passes(self) -> None:
        self.registry.validate_pairwise_overlap([])

    def test_validate_pairwise_overlap_unknown_agent_raises(self) -> None:
        with pytest.raises(RegistryValidationError):
            self.registry.validate_pairwise_overlap(
                [("legal_agent", "v1.0"), ("ghost_agent", "v1.0")]
            )


# ---------------------------------------------------------------------------
# 4. Error type structure
# ---------------------------------------------------------------------------


class TestProjectionBindingError:
    def test_attributes_are_set(self) -> None:
        err = ProjectionBindingError(
            agent_id="legal_agent",
            version="v1.0",
            undeclared_fields=frozenset({IRField.METADATA_BLOCK}),
            declared_scope=frozenset({IRField.ATTRIBUTE}),
        )
        assert err.agent_id == "legal_agent"
        assert err.version == "v1.0"
        assert IRField.METADATA_BLOCK in err.undeclared_fields
        assert IRField.ATTRIBUTE in err.declared_scope

    def test_is_exception(self) -> None:
        err = ProjectionBindingError(
            agent_id="a",
            version="v1",
            undeclared_fields=frozenset({IRField.RELATION}),
            declared_scope=frozenset({IRField.ATTRIBUTE}),
        )
        assert isinstance(err, Exception)

    def test_message_contains_agent_id(self) -> None:
        err = ProjectionBindingError(
            agent_id="legal_agent",
            version="v1.0",
            undeclared_fields=frozenset({IRField.METADATA_BLOCK}),
            declared_scope=frozenset({IRField.ATTRIBUTE}),
        )
        assert "legal_agent" in str(err)


class TestRegistryValidationError:
    def test_detail_attribute_is_set(self) -> None:
        err = RegistryValidationError("missing descriptor for agent_x v1.0")
        assert "missing descriptor" in err.detail

    def test_is_exception(self) -> None:
        assert isinstance(RegistryValidationError("test"), Exception)


class TestCompositionGuardError:
    def test_attributes_are_set(self) -> None:
        err = CompositionGuardError(
            source_agent_id="legal_agent",
            output_type="LegalAnalysisOutput",
            target_type="CanonicalSemanticIR",
        )
        assert err.source_agent_id == "legal_agent"
        assert err.output_type == "LegalAnalysisOutput"
        assert err.target_type == "CanonicalSemanticIR"

    def test_is_exception(self) -> None:
        err = CompositionGuardError("a", "OutputType", "CanonicalSemanticIR")
        assert isinstance(err, Exception)

    def test_message_contains_output_and_target_types(self) -> None:
        err = CompositionGuardError(
            "legal_agent", "LegalAnalysisOutput", "CanonicalSemanticIR"
        )
        assert "LegalAnalysisOutput" in str(err)
        assert "CanonicalSemanticIR" in str(err)


# ---------------------------------------------------------------------------
# 5. Protocol conformance — structural checks via runtime_checkable
# ---------------------------------------------------------------------------


class _ConcreteMonitor:
    """Minimal conforming implementation of IProjectionAccessMonitor."""

    def declare(
        self, agent_id: str, version: str, descriptor: ProjectionDescriptor
    ) -> None:
        pass

    def validate_access(
        self, agent_id: str, version: str, accessed_fields: frozenset[IRField]
    ) -> None:
        pass


class _ConcreteRegistryValidator:
    """Minimal conforming implementation of IProjectionRegistryValidator."""

    def validate_registry(
        self,
        descriptors: dict[tuple[str, str], ProjectionDescriptor],
        overlap_classifications: list[OverlapClassification],
        active_agent_ids: list[tuple[str, str]],
    ) -> None:
        pass


class _ConcreteCompositionGuard:
    """Minimal conforming implementation of ICompositionGuard."""

    def assert_terminal(self, output: Any, source_agent_id: str) -> None:
        pass

    def assert_no_implicit_ir_synthesis(
        self, source_agent_id: str, target_input: Any, target_type_name: str
    ) -> None:
        pass


class _ConcreteAuditEmitter:
    """Minimal conforming implementation of IBindingAuditEmitter."""

    def emit(self, record: BindingAuditRecord) -> None:
        pass

    def emit_equivalence_sweep(
        self,
        descriptors: dict[tuple[str, str], ProjectionDescriptor],
        runtime_state_repr: str,
    ) -> BindingAuditRecord:
        return BindingAuditRecord(
            registry_hash="a",
            runtime_state_hash="b",
            equivalence_status="EQUIVALENT",
            divergence_details="",
            agent_id="",
            binding_hook_id="M4",
        )


class TestProtocolConformance:
    def test_concrete_monitor_satisfies_protocol(self) -> None:
        assert isinstance(_ConcreteMonitor(), IProjectionAccessMonitor)

    def test_concrete_registry_validator_satisfies_protocol(self) -> None:
        assert isinstance(_ConcreteRegistryValidator(), IProjectionRegistryValidator)

    def test_concrete_composition_guard_satisfies_protocol(self) -> None:
        assert isinstance(_ConcreteCompositionGuard(), ICompositionGuard)

    def test_concrete_audit_emitter_satisfies_protocol(self) -> None:
        assert isinstance(_ConcreteAuditEmitter(), IBindingAuditEmitter)

    def test_plain_object_does_not_satisfy_monitor_protocol(self) -> None:
        assert not isinstance(object(), IProjectionAccessMonitor)

    def test_plain_object_does_not_satisfy_registry_validator_protocol(self) -> None:
        assert not isinstance(object(), IProjectionRegistryValidator)

    def test_plain_object_does_not_satisfy_composition_guard_protocol(self) -> None:
        assert not isinstance(object(), ICompositionGuard)

    def test_plain_object_does_not_satisfy_audit_emitter_protocol(self) -> None:
        assert not isinstance(object(), IBindingAuditEmitter)


# ---------------------------------------------------------------------------
# 6. Composition guard semantics (structural, not adapter-level)
# ---------------------------------------------------------------------------


class TestCompositionGuardSemantics:
    """These tests verify compositional boundary rules at the type/spec level,
    independent of any concrete ICompositionGuard implementation.
    """

    def test_legal_analysis_output_type_name_is_not_canonical_ir(self) -> None:
        # The guard must distinguish LegalAnalysisOutput from CanonicalSemanticIR.
        # This test confirms the type names differ — the foundation for the guard.
        from egregore.domain.legal_agent.legal_models import LegalAnalysisOutput
        from egregore.domain.semantics.canonical_ir import CanonicalSemanticIR

        assert LegalAnalysisOutput.__name__ != CanonicalSemanticIR.__name__
        assert LegalAnalysisOutput.__name__ == "LegalAnalysisOutput"
        assert CanonicalSemanticIR.__name__ == "CanonicalSemanticIR"

    def test_legal_analysis_output_does_not_inherit_canonical_ir(self) -> None:
        from egregore.domain.legal_agent.legal_models import LegalAnalysisOutput
        from egregore.domain.semantics.canonical_ir import CanonicalSemanticIR

        assert not issubclass(LegalAnalysisOutput, CanonicalSemanticIR)

    def test_legal_analysis_output_is_frozen(self) -> None:
        """Terminal outputs must be immutable — frozen dataclass prevents field mutation."""
        # Verify it is a frozen dataclass by checking its __dataclass_params__
        import dataclasses

        from egregore.domain.legal_agent.legal_models import LegalAnalysisOutput

        assert dataclasses.is_dataclass(LegalAnalysisOutput)
        params = LegalAnalysisOutput.__dataclass_params__
        assert params.frozen is True

    def test_projection_binding_error_is_raised_not_swallowed(self) -> None:
        """Verifies that ProjectionBindingError propagates out of validate_agent_scope."""
        registry = StaticProjectionRegistry()
        with pytest.raises(ValueError):
            registry.validate_agent_scope(
                "legal_agent",
                "v1.0",
                frozenset({IRField.METADATA_BLOCK, IRField.RELATION}),
            )
