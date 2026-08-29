"""Tests for StrategistAgent (grounded)."""

import pytest
from pathlib import Path
from egregore.application.agents.base import AgentContext
from egregore.application.agents.strategist import StrategistAgent
from egregore.application.strategy.models import StrategyScope
from egregore.interface.anchorum_adapter import AnchorumAdapter
from egregore.assurance.assurance_engine import AssuranceEngine


class MockEngine:
    def analyze(self, ir, case_id):
        return {
            "case_id": case_id,
            "issues_identified": (),
            "applicable_rules": [],
            "supporting_evidence_ids": (),
            "inference_chain": (),
            "confidence_scores": {},
            "uncertainty_flags": (),
            "reasoning_version": "test",
            "agent_version": {
                "rule_registry_version": "v1",
                "inference_engine_version": "v1",
            },
            "prohibited_conclusions": (),
        }


@pytest.fixture
def adapter(tmp_path):
    return AnchorumAdapter(engine=MockEngine(), raw_output_dir=tmp_path / "raw_outputs")


@pytest.fixture
def assurance():
    return AssuranceEngine()


def test_strategist_agent_runs(adapter, assurance):
    scope = StrategyScope(matter_id="MOLSON-2026", goals="assess legal exposure")
    agent = StrategistAgent(adapter, assurance)
    result = agent.run(AgentContext(matter_id="MOLSON-2026", raw_inputs={"scope": scope}))
    assert result.agent_id == "strategist"
    assert "memo" in result.findings
    assert len(result.findings["memo"].analyses) == 18
    assert "text" in result.findings
    assert "STRATEGY MEMO" in result.findings["text"]


def test_strategist_agent_with_dict_scope(adapter, assurance):
    agent = StrategistAgent(adapter, assurance)
    result = agent.run(AgentContext(
        matter_id="X",
        raw_inputs={"scope": {"matter_id": "X", "goals": "test"}}
    ))
    assert result.findings["memo"].matter_id == "X"
