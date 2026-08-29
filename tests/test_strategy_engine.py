"""Tests for Master Strategy Engine (grounded)."""

import pytest
from pathlib import Path
from egregore.application.strategy.models import StrategyScope, Perspective, TimeHorizon, StrategyMemo
from egregore.application.strategy.engine import StrategyEngine
from egregore.application.strategy.memo_builder import render_memo
from egregore.interface.anchorum_adapter import AnchorumAdapter
from egregore.assurance.assurance_engine import AssuranceEngine


class MockEngine:
    def analyze(self, ir, case_id):
        return {
            "case_id": case_id,
            "issues_identified": ("Communication pattern established",),
            "applicable_rules": [],
            "supporting_evidence_ids": tuple(ir.statements[0].source_id for _ in ()),  # empty
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


@pytest.fixture
def scope():
    return StrategyScope(
        matter_id="MOLSON-2026",
        jurisdiction="QC",
        goals="Assess legal exposure and negotiate favorable settlement",
        constraints=("confidential",),
        stakeholders=("client", "opposing counsel"),
        evidence_refs=("doc1", "doc2"),
    )


def test_engine_runs_all_perspectives(adapter, assurance, scope):
    engine = StrategyEngine(adapter, assurance)
    memo = engine.run(scope)
    assert len(memo.analyses) == 18
    assert memo.status == "COMPLETE"


def test_engine_covers_required_perspectives(adapter, assurance, scope):
    engine = StrategyEngine(adapter, assurance)
    memo = engine.run(scope)
    seen_perspectives = {a.perspective for a in memo.analyses}
    assert seen_perspectives == set(Perspective)


def test_memo_rendering_contains_sections(adapter, assurance, scope):
    engine = StrategyEngine(adapter, assurance)
    memo = engine.run(scope)
    text = render_memo(memo)
    assert "STRATEGY MEMO" in text
    assert "LEGAL / SHORT" in text
    assert "Option" in text or "No verified options" in text


def test_engine_isolates_failure(adapter, assurance, monkeypatch):
    from egregore.application.strategy.perspective_agent import StrategyPerspectiveAgent

    def fail_run(self, context):
        raise RuntimeError("boom")

    monkeypatch.setattr(StrategyPerspectiveAgent, "run", fail_run)
    engine = StrategyEngine(adapter, assurance, perspectives=[Perspective.LEGAL])
    scope = StrategyScope(matter_id="X", goals="test")
    memo = engine.run(scope)
    assert memo.status == "DRAFT"
    assert len(memo.analyses) == 0
