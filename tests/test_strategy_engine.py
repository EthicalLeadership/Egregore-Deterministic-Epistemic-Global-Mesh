"""Tests for Master Strategy Engine."""

import pytest
from egregore.application.strategy.models import StrategyScope, Perspective, TimeHorizon, StrategyMemo
from egregore.application.strategy.engine import StrategyEngine
from egregore.application.strategy.memo_builder import render_memo


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


def test_engine_runs_all_perspectives(scope):
    engine = StrategyEngine()
    memo = engine.run(scope)
    # Each perspective x horizon = 6 * 3 = 18 analyses
    assert len(memo.analyses) == 18
    assert memo.status == "COMPLETE"


def test_engine_covers_required_perspectives(scope):
    engine = StrategyEngine()
    memo = engine.run(scope)
    seen_perspectives = {a.perspective for a in memo.analyses}
    assert seen_perspectives == set(Perspective)


def test_memo_rendering_contains_sections(scope):
    engine = StrategyEngine()
    memo = engine.run(scope)
    text = render_memo(memo)
    assert "STRATEGY MEMO" in text
    assert "LEGAL / SHORT" in text
    assert "Option A" in text


def test_engine_isolates_failure(monkeypatch):
    from egregore.application.strategy.perspective_agent import StrategyPerspectiveAgent

    def fail_run(self, context):
        raise RuntimeError("boom")

    # Replace run method for one agent after creation? We'll just monkeypatch the class for a specific perspective.
    monkeypatch.setattr(StrategyPerspectiveAgent, "run", fail_run)
    engine = StrategyEngine(perspectives=[Perspective.LEGAL])
    scope = StrategyScope(matter_id="X", goals="test")
    memo = engine.run(scope)
    assert memo.status == "DRAFT"
    assert len(memo.analyses) == 0
