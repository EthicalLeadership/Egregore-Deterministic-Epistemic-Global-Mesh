"""Tests for DossierAgent."""

import pytest
from pathlib import Path
from egregore.application.agents.base import AgentContext
from egregore.application.agents.dossier import DossierAgent
from egregore.interface.anchorum_adapter import AnchorumAdapter
from egregore.assurance.assurance_engine import AssuranceEngine


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
def workspace(tmp_path):
    (tmp_path / "a.txt").write_text("contract signed on March 3")
    (tmp_path / "b.md").write_text("no relevant content")
    return tmp_path


@pytest.fixture
def adapter(tmp_path):
    return AnchorumAdapter(engine=MockEngine(), raw_output_dir=tmp_path / "raw_outputs")


@pytest.fixture
def assurance():
    return AssuranceEngine()


@pytest.fixture
def agent(workspace, adapter, assurance):
    return DossierAgent(workspace_root=workspace, adapter=adapter, assurance=assurance)


def test_list_files(agent):
    result = agent.run(AgentContext(matter_id="m1", raw_inputs={"action": "list_files"}))
    assert "a.txt" in result.findings["files"]
    assert "b.md" in result.findings["files"]


def test_read_file(agent):
    result = agent.run(AgentContext(matter_id="m1", raw_inputs={"action": "read_file", "path": "a.txt"}))
    assert "March 3" in result.findings["content"]


def test_search_fallback(agent):
    result = agent.run(AgentContext(matter_id="m1", raw_inputs={"action": "search", "query": "March 3", "case_id": "case1"}))
    assert any("a.txt" in r["path"] for r in result.findings["results"])


def test_analyze_action(agent):
    result = agent.run(AgentContext(matter_id="m1", raw_inputs={"action": "analyze", "ir": {"facts": []}, "case_id": "case1"}))
    assert result.findings["case_id"] == "case1"
    assert "raw_output_id" in result.findings
