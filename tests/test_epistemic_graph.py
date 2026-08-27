"""Tests for hardened EpistemicGraph using real Egregore models."""

import pytest
from egregore.domain.legal_agent.legal_models import (
    LegalFact,
    RuleMatch,
    InferenceNode,
    LegalAnalysisOutput,
    LegalAgentVersion,
)
from egregore.domain.epistemic import (
    EpistemicGraph,
    Evidence,
    Proposition,
    Contradiction,
    EpistemicStatus,
    InMemoryContentStore,
)
from egregore.domain.epistemic_mapper import map_legal_output_to_graph


class FakeIR:
    """Minimal IR stand-in to avoid importing CanonicalSemanticIR."""
    def __init__(self, facts):
        self.facts = facts


@pytest.fixture
def content_store():
    return InMemoryContentStore()


def create_test_ir():
    """Create a minimal IR with facts."""
    facts = [
        LegalFact(
            fact_id="fact_1",
            content="Employee sent email on 2023-01-01",
            source_statement_type="fact",
            source_id="email_log",
            confidence_weight=0.9,
        )
    ]
    return FakeIR(facts=facts)


def test_no_orphaned_propositions_in_legal_output(content_store):
    """Invariant: Every Proposition must have evidence or inference chain."""
    ir = create_test_ir()

    rule_match = RuleMatch(
        rule_id="rule_workplace_comms",
        rule_text="Electronic communications in workplace contexts may establish patterns of conduct.",
        jurisdiction="general",
        matched_fact_ids=("fact_1",),
        confidence=0.75,
    )
    node = InferenceNode(
        node_id="node_1",
        premise_rule_ids=("rule_workplace_comms",),
        premise_fact_ids=("fact_1",),
        conclusion="The employee communicated on 2023-01-01.",
        confidence=0.80,
        uncertainty_reason="",
    )
    output = LegalAnalysisOutput(
        case_id="case_001",
        issues_identified=("Communication pattern established",),
        applicable_rules=(rule_match,),
        supporting_evidence_ids=("fact_1",),
        inference_chain=(node,),
        confidence_scores={"fact_1": 0.9, "rule_workplace_comms": 0.75, "node_1": 0.80},
        uncertainty_flags=(),
        reasoning_version="v1.0",
        agent_version=LegalAgentVersion(
            rule_registry_version="v1.0",
            inference_engine_version="v1.0"
        ),
    )

    graph = map_legal_output_to_graph(output, ir, content_store)

    orphans = graph.get_orphaned_propositions()
    assert len(orphans) == 0, f"Found orphaned propositions: {orphans}"


def test_evidence_privacy_no_raw_content(content_store):
    """Evidence must not contain raw content, only hash and ref."""
    fact = LegalFact(
        fact_id="f1",
        content="Sensitive personal data",
        source_statement_type="fact",
        source_id="src1",
        confidence_weight=0.8,
    )
    evidence = Evidence.from_legal_fact(fact, content_store)

    assert not hasattr(evidence, "content")
    assert evidence.content_hash != fact.content
    assert evidence.content_ref != ""


def test_proposition_creation_rejects_orphan():
    with pytest.raises(ValueError):
        Proposition(
            id="p1",
            text="Unsupported claim",
            status=EpistemicStatus.ASSERTED,
            supporting_evidence_ids=frozenset(),
            contradicting_evidence_ids=frozenset(),
            inference_chain=(),
        )


def test_referential_integrity_detects_missing_evidence(content_store):
    """Graph should report dangling references."""
    evidence = Evidence(
        id="ev1",
        content_hash="abc",
        content_ref="ref1",
        source_id="src",
        confidence_weight=0.5,
        epistemic_status=EpistemicStatus.OBSERVED,
    )
    prop = Proposition(
        id="p1",
        text="Claim with missing evidence",
        status=EpistemicStatus.ASSERTED,
        supporting_evidence_ids=frozenset({"missing_ev"}),
    )
    graph = EpistemicGraph(
        case_id="c1",
        evidences=(evidence,),
        propositions=(prop,),
        contradictions=(),
    )
    violations = graph.validate_referential_integrity()
    assert len(violations) == 1
    assert "missing evidence" in violations[0]
