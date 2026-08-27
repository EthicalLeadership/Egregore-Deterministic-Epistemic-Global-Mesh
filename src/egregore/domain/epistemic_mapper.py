"""Mapper: LegalAnalysisOutput → EpistemicGraph.

Strictly domain‑layer: depends only on domain legal models and
epistemic graph.
"""

from __future__ import annotations

from typing import Dict, Any, Optional

from egregore.domain.legal_agent.legal_models import (
    LegalAnalysisOutput,
    LegalFact,
    RuleMatch,
    InferenceNode,
)
from egregore.domain.epistemic import (
    Evidence,
    Proposition,
    EpistemicGraph,
    EpistemicStatus,
    SecureContentStore,
    InMemoryContentStore,
)


def map_legal_output_to_graph(
    output: LegalAnalysisOutput,
    ir: Any,  # Any object with facts/statements attribute
    content_store: Optional[SecureContentStore] = None,
) -> EpistemicGraph:
    """Map LegalAnalysisOutput + source IR to EpistemicGraph.

    Raises:
        ValueError: if an evidence ID referenced in output is not found in IR.
    """
    if content_store is None:
        content_store = InMemoryContentStore()

    # Get facts from IR – try common attribute names
    raw_facts = getattr(ir, "facts", None) or getattr(ir, "statements", None) or []
    fact_map: Dict[str, LegalFact] = {fact.fact_id: fact for fact in raw_facts}

    evidences = []
    for ev_id in output.supporting_evidence_ids:
        fact = fact_map.get(ev_id)
        if fact is None:
            raise ValueError(f"Evidence ID '{ev_id}' referenced in LegalAnalysisOutput but not found in IR")
        evidences.append(Evidence.from_legal_fact(fact, content_store))

    propositions = []
    for rule in output.applicable_rules:
        propositions.append(Proposition.from_rule_match(rule))
    for node in output.inference_chain:
        propositions.append(Proposition.from_inference_node(node))

    return EpistemicGraph(
        case_id=output.case_id,
        evidences=tuple(evidences),
        propositions=tuple(propositions),
        contradictions=(),
        provenance_metadata={
            "reasoning_version": output.reasoning_version,
            "agent_version": str(output.agent_version),
            "uncertainty_flags": ", ".join(output.uncertainty_flags),
        },
    )
