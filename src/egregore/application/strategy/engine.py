"""Master Strategy Engine.

Runs perspective agents concurrently via SwarmManager, compiles analyses,
and produces a StrategyMemo after assurance.
"""

from __future__ import annotations

from typing import List, Dict, Any

from egregore.application.agents.base import AgentContext
from egregore.application.swarm.manager import SwarmManager
from egregore.application.strategy.models import StrategyScope, Perspective, TimeHorizon, PerspectiveAnalysis, StrategyMemo
from egregore.application.strategy.perspective_agent import StrategyPerspectiveAgent


class StrategyEngine:
    """Orchestrates 360° strategy analysis."""

    def __init__(self, perspectives: List[Perspective] | None = None):
        self.perspectives = perspectives or list(Perspective)
        self.horizons = [TimeHorizon.SHORT, TimeHorizon.MEDIUM, TimeHorizon.LONG]

    def run(self, scope: StrategyScope) -> StrategyMemo:
        """Run all perspective/horizon combinations and compile memo."""
        agents = []
        contexts: Dict[str, AgentContext] = {}
        for p in self.perspectives:
            for h in self.horizons:
                agent = StrategyPerspectiveAgent(p, h)
                agents.append(agent)
                contexts[agent.agent_id] = AgentContext(
                    matter_id=scope.matter_id,
                    scope={},
                    raw_inputs={"scope": scope},
                )

        swarm = SwarmManager(agents=agents, max_workers=min(8, len(agents)))
        result = swarm.run(contexts)

        analyses: List[PerspectiveAnalysis] = []
        for r in result.results:
            analysis = r.findings.get("analysis")
            if isinstance(analysis, PerspectiveAnalysis):
                analyses.append(analysis)

        # If any perspective failed, we still continue; errors are captured in swarm.
        summary = f"Strategy memo for {scope.matter_id}: {len(analyses)} perspective analyses completed."

        return StrategyMemo(
            matter_id=scope.matter_id,
            scope=scope,
            analyses=tuple(analyses),
            summary=summary,
            status="DRAFT" if result.has_errors() else "COMPLETE",
        )
