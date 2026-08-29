"""Perspective agent for strategy analysis (Anchorum-grounded)."""

from __future__ import annotations

from typing import Any

from egregore.application.agents.base import BaseAgent, AgentContext, AgentResult
from egregore.application.strategy.models import (
    StrategyScope,
    Perspective,
    TimeHorizon,
    PerspectiveAnalysis,
)
from egregore.domain.semantics.canonical_ir import (
    CanonicalSemanticIR,
    FactStatement,
    SemanticStatementType,
)
from egregore.domain.legal_agent.execution_authority import ExecutionAuthority
from egregore.domain.legal_agent.legal_models import LegalAnalysisOutput
from egregore.domain.epistemic_mapper import map_legal_output_to_graph
from egregore.interface.anchorum_adapter import AnchorumAdapter
from egregore.assurance.assurance_engine import AssuranceEngine


class StrategyPerspectiveAgent(BaseAgent):
    """Generates structured analysis for a single perspective/horizon
    using real Anchorum deterministic/epistemic tools."""

    def __init__(
        self,
        perspective: Perspective,
        horizon: TimeHorizon,
        adapter: AnchorumAdapter,
        assurance: AssuranceEngine,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.perspective = perspective
        self.horizon = horizon
        self.adapter = adapter
        self.assurance = assurance
        self.agent_id = f"strategy_{perspective.value}_{horizon.value}"

    def _build_ir(self, scope: StrategyScope) -> CanonicalSemanticIR:
        """Create a minimal CanonicalSemanticIR from the scope evidence.

        The IR expects `FactStatement` objects, not `LegalFact`.
        """
        statements = []
        for ev_id in scope.evidence_refs:
            content = scope.evidence_contents.get(ev_id, f"Evidence {ev_id}")
            statements.append(
                FactStatement(
                    statement_type=SemanticStatementType.FACT,
                    content=content,
                    source_id=ev_id,
                )
            )
        if not statements:
            statements.append(
                FactStatement(
                    statement_type=SemanticStatementType.FACT,
                    content=f"Scope: {scope.goals}",
                    source_id="scope",
                )
            )
        return CanonicalSemanticIR(
            version_id=f"v1-{scope.matter_id}",
            reasoning_version_id=f"strategy-{self.perspective.value}-{self.horizon.value}",
            statements=tuple(statements),
        )

    def run(self, context: AgentContext) -> AgentResult:
        """Produce analysis based on the scope in context.raw_inputs."""
        scope = context.raw_inputs.get("scope")
        if not isinstance(scope, StrategyScope):
            raise ValueError("StrategyPerspectiveAgent requires 'scope' as StrategyScope")

        # 1. Build a canonical IR from evidence
        ir = self._build_ir(scope)

        # 2. Call real legal reasoning engine via adapter
        with ExecutionAuthority.governed():
            raw = self.adapter.run_legal_analysis(ir=ir, case_id=scope.matter_id)

        # 3. Reconstruct LegalAnalysisOutput from raw payload dict
        output_dict = raw.raw_payload.get("output")
        if not isinstance(output_dict, dict):
            raise TypeError("Adapter output must be dict")
        # Normalize nested dataclasses from dicts
        from egregore.domain.legal_agent.legal_models import (
            LegalAgentVersion,
            RuleMatch,
            InferenceNode,
        )

        av = output_dict.get("agent_version")
        if isinstance(av, dict):
            output_dict["agent_version"] = LegalAgentVersion(**av)

        rules = output_dict.get("applicable_rules")
        if isinstance(rules, (list, tuple)):
            output_dict["applicable_rules"] = tuple(
                RuleMatch(**r) if isinstance(r, dict) else r for r in rules
            )

        nodes = output_dict.get("inference_chain")
        if isinstance(nodes, (list, tuple)):
            output_dict["inference_chain"] = tuple(
                InferenceNode(**n) if isinstance(n, dict) else n for n in nodes
            )

        analysis_output = LegalAnalysisOutput(**output_dict)

        # 4. Map to EpistemicGraph
        graph = map_legal_output_to_graph(analysis_output, ir)

        # 5. Run assurance
        report = self.assurance.run(graph)

        # 6. Build PerspectiveAnalysis from verified data
        findings = [p.text for p in graph.propositions if p.status.value != "UNSUPPORTED"]
        risks = list(analysis_output.uncertainty_flags)
        options = [node.conclusion for node in analysis_output.inference_chain]

        analysis = PerspectiveAnalysis(
            perspective=self.perspective,
            horizon=self.horizon,
            findings=findings or [f"No material findings for {self.perspective.value} / {self.horizon.value}"],
            risks=risks or ["No uncertainty flags"],
            options=options or ["No verified options"],
            evidence_ids=scope.evidence_refs,
            provenance={
                "raw_output_id": raw.id,
                "assurance_status": report.overall_status.value,
                "output_hash": raw.output_hash,
            },
        )

        return AgentResult(
            agent_id=self.agent_id,
            run_id=f"{scope.matter_id}-{self.agent_id}",
            findings={"analysis": analysis},
            raw_anchorum_outputs={"legal_analysis": raw.model_dump()},
            assurance_report=report.model_dump() if report else None,
        )
