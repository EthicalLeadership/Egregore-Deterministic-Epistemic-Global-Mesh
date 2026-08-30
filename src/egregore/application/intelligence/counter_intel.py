"""Counter-Intelligence Agent.

Detects manipulation indicators and produces testable deception hypotheses.
Prefix: [COUNTER-INTEL]
Non-contaminating: outputs are advisory only, never altering operational layers.
"""

from __future__ import annotations

from typing import Any, List, Dict
from datetime import datetime, timezone

from egregore.application.agents.base import BaseAgent, AgentContext, AgentResult
from egregore.application.intelligence.models import (
    ManipulationIndicator,
    DeceptionHypothesis,
    CountermeasureRecommendation,
)
from egregore.domain.artifact.access import AgentArtifactAccessor


class CounterIntelAgent(BaseAgent):
    """Analyzes intelligence for manipulation indicators and recommends countermeasures."""

    agent_id = "counter_intel"
    version = "0.1.0"

    def __init__(self, accessor: AgentArtifactAccessor, **kwargs: Any):
        super().__init__(**kwargs)
        self.accessor = accessor

    def run(self, context: AgentContext) -> AgentResult:
        """Produce counter-intelligence artifacts based on collected intelligence.

        context.raw_inputs may contain:
            - intelligence_report: IntelligenceReport from collector
            - evidence_metadata: dict of evidence_id -> metadata flags (metadata_scrubbed, etc.)
        """
        report = context.raw_inputs.get("intelligence_report")
        evidence_metadata = context.raw_inputs.get("evidence_metadata", {})

        indicators: List[ManipulationIndicator] = []
        hypotheses: List[DeceptionHypothesis] = []
        recommendations: List[CountermeasureRecommendation] = []

        # Check evidence metadata for manipulation patterns
        for ev_id, flags in evidence_metadata.items():
            if flags.get("metadata_scrubbed"):
                indicators.append(
                    ManipulationIndicator(
                        id=f"MI-{ev_id[:8]}",
                        type="metadata_scrubbed",
                        evidence_ids=(ev_id,),
                        confidence="LIKELY",
                        note="Metadata (timestamps) missing or altered.",
                    )
                )
                hypotheses.append(
                    DeceptionHypothesis(
                        id=f"DH-{ev_id[:8]}",
                        text=f"Evidence {ev_id} may have had metadata removed to obscure origin or date.",
                        based_on_claim_ids=(),
                        confirming_evidence=f"Recovered EXIF/header data for {ev_id} showing original timestamp.",
                        refuting_evidence=f"Documented chain-of-custody showing metadata was absent since creation.",
                        confidence="UNCERTAIN",
                    )
                )
                recommendations.append(
                    CountermeasureRecommendation(
                        id=f"CM-{ev_id[:8]}",
                        action=f"Request original, unaltered file for {ev_id} with full metadata.",
                        priority=1,
                        related_hypotheses=(f"DH-{ev_id[:8]}",),
                        note="Preserve current copy as evidence.",
                    )
                )

        # If no indicators, add a benign note
        if not indicators:
            indicators.append(
                ManipulationIndicator(
                    id="MI-NONE",
                    type="none",
                    evidence_ids=(),
                    confidence="CERTAIN",
                    note="No obvious manipulation indicators detected.",
                )
            )

        findings = {
            "indicators": tuple(indicators),
            "hypotheses": tuple(hypotheses),
            "recommendations": tuple(recommendations),
        }

        return AgentResult(
            agent_id=self.agent_id,
            run_id=f"{context.matter_id}-counter-intel",
            findings=findings,
            raw_anchorum_outputs={},
        )
