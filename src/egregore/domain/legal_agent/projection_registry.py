"""Legal Agent v1 projection registry — seeded static descriptor.

This module provides the canonical registered projection descriptor for Legal Agent v1.0
and the StaticProjectionRegistry that holds it.

Design notes:
- The descriptor is the ground truth for CBI-0 M1 enforcement against Legal Agent v1.
- The scope was empirically derived from legal_reasoning_engine._bind_facts() analysis.
  See gate_4_3_constraint_binding_interface.md § 8 "Grounding Against Legal Agent v1".
- This module owns only Legal Agent projection data. Other agents register their own
  descriptors in their own modules.
- The registry is static and immutable in Phase 1. No runtime mutation is permitted.
"""

from __future__ import annotations

from egregore.domain.legal_agent.errors import RegistryValidationError
from egregore.domain.semantics.projection_descriptor import (
    IRField,
    OverlapClassification,
    ProjectionConstraint,
    ProjectionDescriptor,
    SensitivityClass,
)

# FIXME ARCH-VIOLATION: from egregore.interface.constraint_binding_ports import RegistryValidationError
# Port-based replacement needed — was importing forbidden layer


# ---------------------------------------------------------------------------
# Legal Agent v1.0 canonical projection descriptor
# ---------------------------------------------------------------------------

_READ_ONLY = ProjectionConstraint(
    name="read_only",
    description="Agent may not modify, augment, or annotate canonical IR fields.",
)
_EVIDENCE_BOUNDED = ProjectionConstraint(
    name="evidence_bounded",
    description=(
        "Agent conclusions must be anchored to observed evidence statements. "
        "Inferences beyond observed evidence are prohibited."
    ),
)
_NO_DERIVED_OUTPUT = ProjectionConstraint(
    name="no_derived_output",
    description=(
        "Agent output is terminal. Output may not be used as a substitute for canonical IR "
        "in subsequent agent input without explicit re-validation bridge."
    ),
)

LEGAL_AGENT_V1_DESCRIPTOR = ProjectionDescriptor(
    agent_id="legal_agent",
    version="v1.0",
    scope=frozenset(
        {
            IRField.ENTITY_TYPE,  # statement type discrimination across all SemanticStatement subtypes
            IRField.ATTRIBUTE,  # content, source_id (Fact); evidence_reference, interpretation (Evidence);
            # claim (Hypothesis). ClassificationStatement attributes excluded by engine.
            IRField.EVIDENCE_BLOCK,  # EvidenceInterpretationStatement presence and content
        }
    ),
    constraints=frozenset({_READ_ONLY, _EVIDENCE_BOUNDED, _NO_DERIVED_OUTPUT}),
    sensitivity_level=SensitivityClass.STANDARD,
)
"""
Excluded fields (not in scope):

  IRField.RELATION:        no relational field access in v1 IR or Legal Agent v1 logic
  IRField.INFERENCE_NODE:  not present on input side of IR; Legal Agent does not consume prior
                           inference nodes from the IR
  IRField.METADATA_BLOCK:  ir.version_id, ir.reasoning_version_id are consumed by executor only;
                           Legal Agent v1 does not read metadata fields

Additionally, within observed ENTITY_TYPE + ATTRIBUTE scope, the following are excluded:
  - ClassificationStatement.classification
  - ClassificationStatement.confidence
  - EvidenceInterpretationStatement.bounds
  - HypothesisStatement.supporting_evidence_ids
"""


# ---------------------------------------------------------------------------
# Registry implementation
# ---------------------------------------------------------------------------


class StaticProjectionRegistry:
    """Phase 1 static projection registry — Legal Agent v1 only.

    Immutable after construction. No agent may be added or removed at runtime.

    Implements the CBI-0 M1 data layer (descriptor lookup) and the data prerequisite
    for M2 (registry completeness validation by IProjectionRegistryValidator).

    Not an interface implementation — this is a domain repository, not an enforcement
    surface. The enforcement logic lives in the concrete CBI-0 adapters.

    Single-agent invariants:
    - LEGAL_AGENT_V1_DESCRIPTOR is always present.
    - No overlap classifications exist (vacuously satisfied with one agent).
    """

    _REGISTRY: dict[tuple[str, str], ProjectionDescriptor] = {
        ("legal_agent", "v1.0"): LEGAL_AGENT_V1_DESCRIPTOR,
    }

    # Overlap classifications: empty in single-agent baseline.
    # When a second agent is admitted, its descriptor module adds entries here.
    _OVERLAP_CLASSIFICATIONS: list[OverlapClassification] = []

    def get(self, agent_id: str, version: str) -> ProjectionDescriptor:
        """Return the registered descriptor for (agent_id, version).

        Raises:
            RegistryValidationError: if no descriptor is registered.

        """
        key = (agent_id, version)
        descriptor = self._REGISTRY.get(key)
        if descriptor is None:
            raise RegistryValidationError(
                f"No projection descriptor registered for agent {agent_id!r} version {version!r}."
            )
        return descriptor

    def all_descriptors(self) -> dict[tuple[str, str], ProjectionDescriptor]:
        """Return a snapshot of all registered descriptors."""
        return dict(self._REGISTRY)

    def all_overlap_classifications(self) -> list[OverlapClassification]:
        """Return a snapshot of all declared overlap classifications."""
        return list(self._OVERLAP_CLASSIFICATIONS)

    def validate_agent_scope(
        self,
        agent_id: str,
        version: str,
        accessed_fields: frozenset[IRField],
    ) -> None:
        """Verify that accessed_fields ⊆ declared scope for (agent_id, version).

        This is the data-layer component of M1 enforcement. The enforcement surface
        (ProjectionBindingError) is owned by IProjectionAccessMonitor implementations.
        This method returns True/False only; callers decide whether to raise.

        Raises:
            RegistryValidationError: if no descriptor for agent_id/version.
            ValueError: on undeclared field access (caller wraps into ProjectionBindingError
                if appropriate for the enforcement context).

        """
        descriptor = self.get(agent_id, version)
        undeclared = accessed_fields - descriptor.scope
        if undeclared:
            raise ValueError(
                f"Agent {agent_id!r} v{version} accessed undeclared IR fields: "
                f"{sorted(f.value for f in undeclared)}. "
                f"Declared scope: {sorted(f.value for f in descriptor.scope)}."
            )

    def validate_pairwise_overlap(
        self,
        active_agent_ids: list[tuple[str, str]],
    ) -> None:
        """Validate that all active agent pairs have overlap classifications.

        With one agent, this is vacuously satisfied and returns immediately.
        With two or more agents, every pair (A, B) where scope(A) ∩ scope(B) ≠ ∅
        must have a registered OverlapClassification.

        Raises:
            RegistryValidationError: if any required pair lacks a classification.

        """
        if len(active_agent_ids) < 2:
            return  # vacuously satisfied

        # Build scope index for overlap computation.
        scope_index: dict[tuple[str, str], frozenset[IRField]] = {}
        for agent_id, version in active_agent_ids:
            descriptor = self.get(agent_id, version)
            scope_index[(agent_id, version)] = descriptor.scope

        # Build classification lookup.
        classified_pairs: set[frozenset[tuple[str, str]]] = set()
        for classification in self._OVERLAP_CLASSIFICATIONS:
            pair_key: frozenset[tuple[str, str]] = frozenset(
                {
                    (classification.agent_id_a, ""),
                    (classification.agent_id_b, ""),
                }
            )
            classified_pairs.add(pair_key)

        # Check every pair.
        pairs = list(active_agent_ids)
        for i, a in enumerate(pairs):
            for b in pairs[i + 1 :]:
                overlap = scope_index[a] & scope_index[b]
                if overlap:
                    a_id, b_id = a[0], b[0]
                    pair_key = frozenset({(a_id, ""), (b_id, "")})
                    if pair_key not in classified_pairs:
                        raise RegistryValidationError(
                            f"Non-disjoint agent pair ({a_id!r}, {b_id!r}) has no overlap "
                            f"classification. Overlapping fields: "
                            f"{sorted(f.value for f in overlap)}."
                        )
