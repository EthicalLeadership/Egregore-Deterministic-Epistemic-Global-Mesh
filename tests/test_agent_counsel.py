"""Tests for Counsel agent."""

import pytest
from pathlib import Path
from egregore.application.agents.base import AgentContext
from egregore.application.agents.counsel import CounselAgent
from egregore.interface.anchorum_adapter import AnchorumAdapter, RawAnchorumOutput
from egregore.assurance.assurance_engine import AssuranceEngine
from egregore.domain.epistemic import EpistemicGraph
from egregore.constitution import ConstitutionalViolation


class MockEngine:
    def analyze(self, ir, case_id):
        return {
            "case_id": case_id,
            "issues_identified": (),
            "applicable_rules": [],
            "supporting_evidence_ids": [],
            "inference_chain": [],
            "confidence_scores": {},
            "uncertainty_flags": [],
            "reasoning_version": "test",
            "agent_version": "test",
            "prohibited_conclusions": (),
        }


@pytest.fixture
def adapter(tmp_path):
    return AnchorumAdapter(engine=MockEngine(), raw_output_dir=tmp_path / "raw_outputs")


@pytest.fixture
def assurance():
    return AssuranceEngine()


def test_counsel_agent_runs(adapter, assurance):
    agent = CounselAgent(adapter=adapter, assurance=assurance)
    context = AgentContext(
        matter_id="matter-1",
        scope={},
    raw_inputs={"ir": {"facts": []}, "case_id": "case-1"},
    )
    result = agent.run(context)
    assert result.agent_id == "counsel"
    assert result.findings["case_id"] == "case-1"
    assert "raw_output_id" in result.findings
    assert "assurance_status" in result.findings


def test_counsel_agent_does_not_self_certify(adapter, assurance):
    agent = CounselAgent(adapter=adapter, assurance=assurance)
    # Try to call a method that would self-certify; should not exist
    with pytest.raises(AttributeError):
        agent.certify()


def test_counsel_agent_does_not_release_artifact(adapter, assurance):
    agent = CounselAgent(adapter=adapter, assurance=assurance)
    with pytest.raises(AttributeError):
        agent.release()
