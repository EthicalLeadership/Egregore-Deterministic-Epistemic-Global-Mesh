"""Tests for AssuranceEngine with basic controls."""

import pytest
from egregore.domain.epistemic import (
    EpistemicGraph,
    Evidence,
    Proposition,
    EpistemicStatus,
    InMemoryContentStore,
)
from egregore.assurance.assurance_engine import (
    AssuranceEngine,
    control_referential_integrity,
    control_no_orphan_propositions,
    control_no_unsupported_propositions,
)
from egregore.assurance.control_models import ControlStatus


def make_evidence(ev_id="ev1"):
    return Evidence(
        id=ev_id,
        content_hash="abc",
        content_ref="ref",
        source_id="src",
        confidence_weight=0.7,
        epistemic_status=EpistemicStatus.OBSERVED,
    )


def test_referential_integrity_passes_on_valid_graph():
    ev = make_evidence()
    prop = Proposition(
        id="p1",
        text="Supported claim",
        status=EpistemicStatus.ASSERTED,
        supporting_evidence_ids=frozenset({"ev1"}),
    )
    graph = EpistemicGraph(
        case_id="c1",
        evidences=(ev,),
        propositions=(prop,),
        contradictions=(),
    )
    result = control_referential_integrity(graph)
    assert result.status == ControlStatus.PASS


def test_referential_integrity_fails_on_missing_evidence():
    prop = Proposition(
        id="p1",
        text="Claim with missing evidence",
        status=EpistemicStatus.ASSERTED,
        supporting_evidence_ids=frozenset({"missing_ev"}),
    )
    graph = EpistemicGraph(
        case_id="c1",
        evidences=(),
        propositions=(prop,),
        contradictions=(),
    )
    result = control_referential_integrity(graph)
    assert result.status == ControlStatus.BLOCKER



def test_engine_runs_and_returns_report():
    ev = make_evidence()
    prop = Proposition(
        id="p1",
        text="Supported claim",
        status=EpistemicStatus.ASSERTED,
        supporting_evidence_ids=frozenset({"ev1"}),
    )
    graph = EpistemicGraph(
        case_id="c1",
        evidences=(ev,),
        propositions=(prop,),
        contradictions=(),
    )
    engine = AssuranceEngine()
    report = engine.run(graph)
    assert report.case_id == "c1"
    assert len(report.controls) == 3
    assert report.overall_status == ControlStatus.PASS


def test_control_no_unsupported_propositions_fails_on_unsupported():
    ev = make_evidence("ev1")
    prop = Proposition(
        id="p1",
        text="Unsupported claim",
        status=EpistemicStatus.UNSUPPORTED,
        supporting_evidence_ids=frozenset({"ev1"}),
    )
    graph = EpistemicGraph(
        case_id="c1",
        evidences=(ev,),
        propositions=(prop,),
        contradictions=(),
    )
    result = control_no_unsupported_propositions(graph)
    assert result.status == ControlStatus.FAIL
