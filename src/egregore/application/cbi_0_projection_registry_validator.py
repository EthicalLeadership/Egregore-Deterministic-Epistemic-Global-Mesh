from __future__ import annotations

from dataclasses import dataclass

from egregore.domain.semantics.projection_descriptor import (
    OverlapClass,
    OverlapClassification,
    ProjectionDescriptor,
)
from egregore.interface.constraint_binding_ports import RegistryValidationError


@dataclass(frozen=True)
class ProjectionRegistryValidator:
    """
    Concrete CBI-0 M2 registry validator.

    Enforces:
    - every active (agent_id, version) has a descriptor
    - for each non-empty overlap between active agent scopes, a corresponding
      overlap classification exists
    - overlap classification consistency checks for the structural classes
      we can validate from scopes (DISJOINT, EQUIVALENT, DEPENDENT)

    Notes:
    - OverlapClassification in this repo is keyed only by agent_id_a/agent_id_b
      (no version). This validator assumes versions are compatible for overlap
      classification purposes in the active orchestration run.

    """

    def validate_registry(  # noqa: C901
        self,
        *,
        descriptors: dict[tuple[str, str], ProjectionDescriptor],
        overlap_classifications: list[OverlapClassification],
        active_agent_ids: list[tuple[str, str]],
    ) -> None:
        # M2: descriptor presence
        missing: list[tuple[str, str]] = []
        for agent_id, version in active_agent_ids:
            if (agent_id, version) not in descriptors:
                missing.append((agent_id, version))
        if missing:
            raise RegistryValidationError(
                f"Missing projection descriptors for active agents: {missing}"
            )

        # Index overlap classifications by unordered agent-id pair.
        # We normalize by using frozenset of {agent_id_a, agent_id_b}.
        classification_index: dict[frozenset[str], OverlapClassification] = {}
        for cls in overlap_classifications:
            key = frozenset({cls.agent_id_a, cls.agent_id_b})
            classification_index[key] = cls

        active_pairs = list(active_agent_ids)
        for i, (a_id, a_ver) in enumerate(active_pairs):
            for b_id, b_ver in active_pairs[i + 1 :]:
                a_desc = descriptors[(a_id, a_ver)]
                b_desc = descriptors[(b_id, b_ver)]
                overlap = a_desc.scope.intersection(b_desc.scope)

                cls = classification_index.get(frozenset({a_id, b_id}))

                if not overlap:
                    # For disjoint pairs, classification may be absent. If present,
                    # it must be explicitly DISJOINT.
                    if cls is not None and cls.overlap_class != OverlapClass.DISJOINT:
                        raise RegistryValidationError(
                            f"Pair ({a_id!r} v{a_ver!r}, {b_id!r} v{b_ver!r}) "
                            f"has empty computed overlap but classification is {cls.overlap_class!r}"
                        )
                    continue

                # Non-empty overlap => must have classification.
                if cls is None:
                    raise RegistryValidationError(
                        f"Non-empty computed overlap for pair ({a_id!r}, {b_id!r}) "
                        f"but no overlap classification exists. Overlap: {sorted(f.value for f in overlap)}"
                    )

                # DISJOINT cannot be used when computed overlap is non-empty.
                if cls.overlap_class == OverlapClass.DISJOINT:
                    raise RegistryValidationError(
                        f"Pair ({a_id!r}, {b_id!r}) computed non-empty overlap "
                        f"but classification is DISJOINT. Overlap: {sorted(f.value for f in overlap)}"
                    )

                # Strong consistency for structural classes we can derive from scopes.
                if (
                    cls.overlap_class == OverlapClass.EQUIVALENT
                    and a_desc.scope != b_desc.scope
                ):
                    raise RegistryValidationError(
                        f"Pair ({a_id!r}, {b_id!r}) classified EQUIVALENT "
                        f"but scopes differ. a_scope={sorted(f.value for f in a_desc.scope)} "
                        f"b_scope={sorted(f.value for f in b_desc.scope)}"
                    )

                if cls.overlap_class == OverlapClass.DEPENDENT:
                    # Docs require a strict subset relation.
                    a_subset_b = a_desc.scope < b_desc.scope
                    b_subset_a = b_desc.scope < a_desc.scope
                    if not (a_subset_b or b_subset_a):
                        raise RegistryValidationError(
                            f"Pair ({a_id!r}, {b_id!r}) classified DEPENDENT "
                            f"but neither scope is a strict subset."
                        )

                # For INTERFERENCE_PRONE / CONFLICT_SENSITIVE we only validate that overlap exists,
                # and (for CONFLICT_SENSITIVE) the dataclass already requires arbitration_policy_ref.
                if (
                    cls.overlap_class
                    in {
                        OverlapClass.INTERFERENCE_PRONE,
                        OverlapClass.CONFLICT_SENSITIVE,
                    }
                    and not overlap
                ):
                    raise RegistryValidationError(
                        f"Pair ({a_id!r}, {b_id!r}) classification {cls.overlap_class!r} "
                        f"but computed overlap is empty."
                    )
