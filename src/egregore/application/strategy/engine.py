"""Master Strategy Engine (Anchorum-grounded)."""

from __future__ import annotations

from typing import List, Dict, Any, Optional

from egregore.application.agents.base import AgentContext
from egregore.application.swarm.manager import SwarmManager
from egregore.application.strategy.models import StrategyScope, Perspective, TimeHorizon, PerspectiveAnalysis, StrategyMemo
from egregore.application.strategy.perspective_agent import StrategyPerspectiveAgent
from egregore.interface.anchorum_adapter import AnchorumAdapter
from egregore.assurance.assurance_engine import AssuranceEngine


class StrategyEngine:
    """Orchestrates 360° strategy analysis with real tools."""

    def __init__(
        self,
        adapter: AnchorumAdapter,
        assurance: AssuranceEngine,
        perspectives: List[Perspective] | None = None,
    ):
        self.adapter = adapter
        self.assurance = assurance
        self.perspectives = perspectives or list(Perspective)
        self.horizons = [TimeHorizon.SHORT, TimeHorizon.MEDIUM, TimeHorizon.LONG]

    def run(self, scope: StrategyScope) -> StrategyMemo:
        """Run all perspective/horizon combinations and compile memo."""
        agents = []
        contexts: Dict[str, AgentContext] = {}
        for p in self.perspectives:
            for h in self.horizons:
                agent = StrategyPerspectiveAgent(p, h, self.adapter, self.assurance)
                agents.append(agent)
                contexts[agent.agent_id] = AgentContext(
                    matter_id=scope.matter_id,
                    scope={},
                    raw_inputs={"scope": scope},
                )

        swarm = SwarmManager(agents=agents, max_workers=min(8, len(agents)))
        result = swarm.run(contexts)

        analyses: List[PerspectiveAnalysis] = []
        raw_outputs = {}
        assurance_reports = {}
        for r in result.results:
            analysis = r.findings.get("analysis")
            if isinstance(analysis, PerspectiveAnalysis):
                analyses.append(analysis)
            if r.raw_anchorum_outputs:
                raw_outputs[r.agent_id] = r.raw_anchorum_outputs
            if r.assurance_report:
                assurance_reports[r.agent_id] = r.assurance_report

        summary = (
            f"Strategy memo for {scope.matter_id}: "
            f"{len(analyses)} perspective analyses completed, "
            f"{len(result.errors)} errors."
        )

        return StrategyMemo(
            matter_id=scope.matter_id,
            scope=scope,
            analyses=tuple(analyses),
            summary=summary,
            status="DRAFT" if result.has_errors() else "COMPLETE",
        )
