"""Tests for StrategistAgent."""

import pytest
from egregore.application.agents.base import AgentContext
from egregore.application.agents.strategist import StrategistAgent
from egregore.application.strategy.models import StrategyScope


def test_strategist_agent_runs():
    scope = StrategyScope(matter_id="MOLSON-2026", goals="assess legal exposure")
    agent = StrategistAgent()
    result = agent.run(AgentContext(matter_id="MOLSON-2026", raw_inputs={"scope": scope}))
    assert result.agent_id == "strategist"
    assert "memo" in result.findings
    assert len(result.findings["memo"].analyses) == 18
    assert "text" in result.findings
    assert "STRATEGY MEMO" in result.findings["text"]


def test_strategist_agent_with_dict_scope():
    agent = StrategistAgent()
    result = agent.run(AgentContext(
        matter_id="X",
        raw_inputs={"scope": {"matter_id": "X", "goals": "test"}}
    ))
    assert result.findings["memo"].matter_id == "X"
