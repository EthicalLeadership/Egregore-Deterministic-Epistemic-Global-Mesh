"""Tests for SwarmManager."""

import pytest
from egregore.application.agents.base import BaseAgent, AgentContext, AgentResult
from egregore.application.swarm.manager import SwarmManager, SwarmError


class GoodAgent(BaseAgent):
    agent_id = "good"
    def run(self, context):
        return AgentResult(agent_id=self.agent_id, run_id="r1", findings={"ok": True})


class AnotherGoodAgent(BaseAgent):
    agent_id = "another"
    def run(self, context):
        return AgentResult(agent_id=self.agent_id, run_id="r2", findings={"ok": True})


class BadAgent(BaseAgent):
    agent_id = "bad"
    def run(self, context):
        raise RuntimeError("boom")


def test_swarm_runs_agents_in_parallel():
    ctx = AgentContext(matter_id="m1", raw_inputs={})
    manager = SwarmManager(agents=[GoodAgent(), AnotherGoodAgent()], max_workers=2)
    result = manager.run({"good": ctx, "another": ctx})
    assert len(result.results) == 2
    assert result.get("good") is not None
    assert result.get("another") is not None
    assert not result.has_errors()


def test_swarm_isolates_failures():
    ctx = AgentContext(matter_id="m1", raw_inputs={})
    manager = SwarmManager(agents=[GoodAgent(), BadAgent()], max_workers=2)
    result = manager.run({"good": ctx, "bad": ctx})
    assert len(result.results) == 1
    assert "bad" in result.errors
    assert result.errors["bad"] == "boom"


def test_swarm_mismatched_agents_raises():
    ctx = AgentContext(matter_id="m1", raw_inputs={})
    manager = SwarmManager(agents=[GoodAgent()], max_workers=1)
    with pytest.raises(SwarmError):
        manager.run({"good": ctx, "extra": ctx})
