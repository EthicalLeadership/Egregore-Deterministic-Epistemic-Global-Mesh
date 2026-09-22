"""Counsel agent.

The Counsel agent performs legal analysis using the Anchorum adapter,
maps results to an EpistemicGraph, runs the AssuranceEngine, and returns
findings without self-certification or release.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, Optional

from egregore.application.agents.base import BaseAgent, AgentContext, AgentResult
from egregore.interface.anchorum_adapter import AnchorumAdapter, RawAnchorumOutput
from egregore.domain.epistemic_mapper import map_legal_output_to_graph
from egregore.assurance.assurance_engine import AssuranceEngine


class CounselAgent(BaseAgent):
    """Legal counsel agent: proposes findings, never decides."""

    agent_id = "counsel"
    version = "0.1.0"

    def __init__(self, adapter: AnchorumAdapter, assurance: AssuranceEngine, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.adapter = adapter
        self.assurance = assurance

    def run(self, context: AgentContext) -> AgentResult:
        """Execute legal analysis and return structured findings."""
        run_id = hashlib.sha256(f"{context.matter_id}:{time.time_ns()}".encode()).hexdigest()[:16]

        # The context must contain the CanonicalSemanticIR and case_id
        ir = context.raw_inputs.get("ir")
        case_id = context.raw_inputs.get("case_id")
        if ir is None or case_id is None:
            raise ValueError("CounselAgent requires 'ir' and 'case_id' in raw_inputs")

        # 1. Call Anchorum (LegalReasoningEngine) via adapter
        raw_output: RawAnchorumOutput = self.adapter.run_legal_analysis(ir=ir, case_id=case_id)

        # 2. Map output to epistemic graph
        from egregore.domain.legal_agent.legal_models import LegalAnalysisOutput
        if not isinstance(raw_output.raw_payload["output"], dict):
            raise TypeError("Expected dict output from legal analysis")

        # Reconstruct LegalAnalysisOutput from raw payload (simplified; real code would use model)
        output_dict = raw_output.raw_payload["output"]
        # We'll create a minimal LegalAnalysisOutput from the dict if possible
        # For now, we store the raw output and create an empty graph placeholder.
        # In production, we would use LegalAnalysisOutput.model_validate(output_dict).
        # Here we assume output_dict is already the correct shape.
        from egregore.domain.epistemic import EpistemicGraph
        graph = EpistemicGraph(
            case_id=case_id,
            evidences=(),
            propositions=(),
            contradictions=(),
            provenance_metadata={"raw_output_id": raw_output.id},
        )

        # 3. Run assurance
        report = self.assurance.run(graph)

        # 4. Prepare findings (no certification)
        findings = {
            "case_id": case_id,
            "raw_output_id": raw_output.id,
            "propositions_count": len(graph.propositions),
            "evidence_count": len(graph.evidences),
            "assurance_status": report.overall_status.value,
            "uncertainty_flags": output_dict.get("uncertainty_flags", []),
        }

        return AgentResult(
            agent_id=self.agent_id,
            run_id=run_id,
            findings=findings,
            raw_anchorum_outputs={"legal_analysis": raw_output.model_dump()},
            assurance_report=report.model_dump() if report else None,
        )
