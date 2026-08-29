"""Strategist agent: produces 360° strategy memos via StrategyEngine."""

from __future__ import annotations

from typing import Any

from egregore.application.agents.base import BaseAgent, AgentContext, AgentResult
from egregore.application.strategy.engine import StrategyEngine
from egregore.application.strategy.memo_builder import render_memo
from egregore.application.strategy.models import StrategyScope


class StrategistAgent(BaseAgent):
    """Elite strategy agent using deterministic/epistemic tools."""

    agent_id = "strategist"
    version = "0.1.0"

    def __init__(self, engine: StrategyEngine | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.engine = engine or StrategyEngine()

    def run(self, context: AgentContext) -> AgentResult:
        """Run the strategy engine on the scope from raw_inputs."""
        scope_data = context.raw_inputs.get("scope")
        if isinstance(scope_data, StrategyScope):
            scope = scope_data
        elif isinstance(scope_data, dict):
            scope = StrategyScope(**scope_data)
        else:
            raise ValueError("StrategistAgent requires 'scope' in raw_inputs")

        memo = self.engine.run(scope)
        return AgentResult(
            agent_id=self.agent_id,
            run_id=f"{scope.matter_id}-strategist",
            findings={"memo": memo, "text": render_memo(memo)},
            raw_anchorum_outputs={},
        )
