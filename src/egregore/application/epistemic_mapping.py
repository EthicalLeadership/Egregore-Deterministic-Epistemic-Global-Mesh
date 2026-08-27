"""Application-layer mapping: RFE Report → EpistemicGraph.

Duck‑typed to avoid importing rfe directly, preserving layer boundaries.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from egregore.domain.epistemic import (
    Evidence,
    Proposition,
    Contradiction,
    EpistemicGraph,
    EpistemicStatus,
    SecureContentStore,
    InMemoryContentStore,
)


def map_rfe_report_to_graph(
    report: Any,
    content_store: Optional[SecureContentStore] = None,
) -> EpistemicGraph:
    """Map RFE Report to EpistemicGraph, using duck typing."""
    if content_store is None:
        content_store = InMemoryContentStore()

    decision_log = report.decision_log

    evidences = []
    propositions = []
    contradictions = []

    # scored_streams are objects with stream_id, confidence, source_tier, authority_weight, decay_method
    for stream in decision_log.scored_streams:
        evidences.append(
            Evidence(
                id=stream.stream_id,
                content_hash="",
                content_ref="",
                source_id=stream.stream_id,
                confidence_weight=stream.confidence,
                epistemic_status=EpistemicStatus.OBSERVED,
                provenance={
                    "source_tier": str(stream.source_tier),
                    "authority_weight": str(stream.authority_weight),
                    "decay_method": stream.decay_method,
                },
            )
        )

    # baseline_conclusions is a list of strings
    for conclusion in decision_log.baseline_conclusions:
        supporting = frozenset(stream.stream_id for stream in decision_log.scored_streams)
        propositions.append(
            Proposition(
                id=f"prop-concl-{hashlib.sha1(conclusion.encode()).hexdigest()[:8]}",
                text=conclusion,
                status=EpistemicStatus.ASSERTED,
                supporting_evidence_ids=supporting,
                contradicting_evidence_ids=frozenset(),
                inference_chain=(),
                provenance={"source": "RFE_baseline"},
            )
        )

    # conflicts have conflict_id, resolved, winning_stream_id, loser_stream_ids, rationale
    for conflict in decision_log.conflicts:
        contradictions.append(Contradiction.from_conflict_resolution(conflict))

    return EpistemicGraph(
        case_id=report.case_id,
        evidences=tuple(evidences),
        propositions=tuple(propositions),
        contradictions=tuple(contradictions),
        provenance_metadata={
            "engine_version": report.engine_version,
            "policy_version": report.policy_version,
            "decision_log_hash": report.decision_log_hash,
        },
    )
