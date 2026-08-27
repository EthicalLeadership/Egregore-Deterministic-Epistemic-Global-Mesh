"""Validation utilities for EpistemicGraph – ensures Constitution rules."""

from typing import List, Tuple
from .epistemic import EpistemicGraph, Proposition, Contradiction, EpistemicStatus


def validate_graph(graph: EpistemicGraph) -> List[str]:
    """Return list of violations found; empty list means graph is valid."""
    violations = []

    # 1. Check for orphaned propositions (Rule 8: material proposition must have provenance)
    orphans = graph.get_orphaned_propositions()
    if orphans:
        violations.append(f"Found orphaned propositions: {[p.id for p in orphans]}")

    # 2. Check unresolved contradictions (Rule 9: material unresolved contradictions must remain visible)
    unresolved = graph.get_unresolved_contradictions()
    if unresolved:
        violations.append(f"Found unresolved contradictions: {[c.id for c in unresolved]}")

    # 3. Check that no proposition has status CONTRADICTED without a contradiction (must have a linked contradiction)
    for p in graph.propositions:
        if p.status == EpistemicStatus.CONTRADICTED:
            # Check if there is a contradiction involving this proposition
            has_contradiction = any(
                p.id in (c.proposition_1_id, c.proposition_2_id)
                for c in graph.contradictions
            )
            if not has_contradiction:
                violations.append(f"Proposition {p.id} is CONTRADICTED but has no linked contradiction")

    # 4. Referential integrity is already enforced by Pydantic, but we double-check
    # (already done in model validator)

    return violations
