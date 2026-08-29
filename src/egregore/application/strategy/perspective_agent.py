"""Perspective agent for strategy analysis."""

from __future__ import annotations

from typing import Any

from egregore.application.agents.base import BaseAgent, AgentContext, AgentResult
from egregore.application.strategy.models import StrategyScope, Perspective, TimeHorizon, PerspectiveAnalysis


class StrategyPerspectiveAgent(BaseAgent):
    """Generates structured analysis for a single perspective/horizon."""

    def __init__(self, perspective: Perspective, horizon: TimeHorizon, **kwargs: Any):
        super().__init__(**kwargs)
        self.perspective = perspective
        self.horizon = horizon
        self.agent_id = f"strategy_{perspective.value}_{horizon.value}"

    def run(self, context: AgentContext) -> AgentResult:
        """Produce analysis based on the scope in context.raw_inputs."""
        scope = context.raw_inputs.get("scope")
        if not isinstance(scope, StrategyScope):
            raise ValueError("StrategyPerspectiveAgent requires 'scope' as StrategyScope")

        # Simulated deterministic analysis from tools.
        # In production, this would call Anchorum deterministic/epistemic functions.
        analysis = PerspectiveAnalysis(
            perspective=self.perspective,
            horizon=self.horizon,
            findings=[
                f"Perspective {self.perspective.value} for {scope.matter_id}",
                f"Short-term implications under {self.horizon.value} horizon",
            ],
            risks=["Uncertainty in evidence" if not scope.evidence_refs else "Evidence available"],
            options=["Option A", "Option B"],
            evidence_ids=scope.evidence_refs,
            provenance={"agent": self.agent_id, "engine": "phase9"},
        )

        return AgentResult(
            agent_id=self.agent_id,
            run_id=f"{scope.matter_id}-{self.agent_id}",
            findings={"analysis": analysis},
            raw_anchorum_outputs={},
        )
