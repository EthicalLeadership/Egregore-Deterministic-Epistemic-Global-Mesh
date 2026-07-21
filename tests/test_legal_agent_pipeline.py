"""Legal Agent pipeline unit tests.

Tests each of the 4 pipeline stages in isolation plus full-pipeline properties:
- Stage isolation (each stage can be driven independently)
- Determinism (same IR + same version → identical output)
- Fail-closed (raises LegalReasoningError; no partial output)
- Structural invariants (prohibited_conclusions always (), confidence in bounds)
- ClassificationStatement excluded from legal facts
"""

from __future__ import annotations

import pytest

from egregore.application.legal_reasoning_engine import (
    LegalReasoningEngine,
    LegalReasoningError,
)
from egregore.domain.legal_agent.execution_authority import ExecutionAuthority
from egregore.domain.legal_agent.legal_models import (
    LegalAgentVersion,
    LegalAnalysisOutput,
    LegalFact,
)
from egregore.domain.legal_agent.rule_registry import StaticRuleRegistry
from egregore.domain.semantics.canonical_ir import (
    CanonicalSemanticIR,
    ClassificationStatement,
    EvidenceInterpretationStatement,
    FactStatement,
    HypothesisStatement,
    SemanticStatementType,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_AGENT_VERSION = LegalAgentVersion(
    rule_registry_version="v1.0",
    inference_engine_version="v1.0",
)


@pytest.fixture(autouse=True)
def _governed_execution_authority():
    """
    Pipeline unit tests validate the reasoning semantics; they are expected
    to run inside the governed scope.
    """
    with ExecutionAuthority.governed():
        yield


def _make_engine() -> LegalReasoningEngine:
    return LegalReasoningEngine(
        rule_registry=StaticRuleRegistry(),
        agent_version=_AGENT_VERSION,
    )


def _make_ir(statements: tuple) -> CanonicalSemanticIR:
    return CanonicalSemanticIR(
        version_id="ir-test-001",
        reasoning_version_id="rrid-test-001",
        statements=statements,
    )


def _analyze_governed(
    engine: LegalReasoningEngine, ir: CanonicalSemanticIR, *, case_id: str
) -> LegalAnalysisOutput:
    with ExecutionAuthority.governed():
        return engine.analyze(ir, case_id=case_id)


# ---------------------------------------------------------------------------
# Stage 1: Fact binding
# ---------------------------------------------------------------------------


class TestBindFacts:

    def test_fact_statement_projected_with_weight_1(self):
        engine = _make_engine()
        ir = _make_ir(
            (FactStatement(content="Email was sent on Monday.", source_id="s1"),)
        )
        facts = engine._bind_facts(ir)
        assert len(facts) == 1
        assert facts[0].confidence_weight == 1.0
        assert facts[0].source_statement_type == SemanticStatementType.FACT.value
        assert facts[0].content == "Email was sent on Monday."

    def test_evidence_interpretation_projected_with_weight_07(self):
        engine = _make_engine()
        ir = _make_ir(
            (
                EvidenceInterpretationStatement(
                    evidence_reference="ev-001",
                    interpretation="The email may indicate communication patterns.",
                    bounds="may_indicate",
                ),
            )
        )
        facts = engine._bind_facts(ir)
        assert len(facts) == 1
        assert facts[0].confidence_weight == 0.7
        assert (
            facts[0].source_statement_type
            == SemanticStatementType.EVIDENCE_INTERPRETATION.value
        )

    def test_hypothesis_projected_with_weight_04(self):
        engine = _make_engine()
        ir = _make_ir(
            (HypothesisStatement(claim="The document was destroyed deliberately."),)
        )
        facts = engine._bind_facts(ir)
        assert len(facts) == 1
        assert facts[0].confidence_weight == 0.4
        assert facts[0].source_statement_type == SemanticStatementType.HYPOTHESIS.value

    def test_classification_statement_excluded(self):
        """ClassificationStatement is routing metadata — must not become a legal fact."""
        engine = _make_engine()
        ir = _make_ir(
            (
                ClassificationStatement(classification="high_priority", confidence=0.9),
                FactStatement(content="A record was retained.", source_id="s2"),
            )
        )
        facts = engine._bind_facts(ir)
        assert len(facts) == 1
        assert facts[0].source_statement_type == SemanticStatementType.FACT.value

    def test_empty_ir_produces_empty_facts(self):
        engine = _make_engine()
        ir = _make_ir(())
        facts = engine._bind_facts(ir)
        assert facts == []

    def test_mixed_statements_all_projected_except_classification(self):
        engine = _make_engine()
        ir = _make_ir(
            (
                FactStatement(content="Document deleted.", source_id="s1"),
                ClassificationStatement(classification="low_risk", confidence=0.1),
                HypothesisStatement(claim="Deletion was intentional."),
                EvidenceInterpretationStatement(
                    evidence_reference="ev-002",
                    interpretation="Retention policy was applicable.",
                    bounds="may_indicate",
                ),
            )
        )
        facts = engine._bind_facts(ir)
        assert len(facts) == 3
        types = {f.source_statement_type for f in facts}
        assert SemanticStatementType.CLASSIFICATION.value not in types


# ---------------------------------------------------------------------------
# Stage 2: Rule mapping
# ---------------------------------------------------------------------------


class TestMapRules:

    def test_communication_keyword_triggers_workplace_comms_rule(self):
        engine = _make_engine()
        facts = [LegalFact("f1", "An email was sent.", "fact", "s1", 1.0)]
        rules = engine._map_rules(facts)
        rule_ids = {r.rule_id for r in rules}
        assert "rule_workplace_comms" in rule_ids

    def test_no_keyword_match_returns_empty(self):
        engine = _make_engine()
        facts = [LegalFact("f1", "The cat sat on the mat.", "fact", "s1", 1.0)]
        rules = engine._map_rules(facts)
        assert rules == []

    def test_retention_keyword_triggers_document_retention_rule(self):
        engine = _make_engine()
        facts = [LegalFact("f1", "Records must be preserved.", "fact", "s1", 1.0)]
        rules = engine._map_rules(facts)
        rule_ids = {r.rule_id for r in rules}
        assert "rule_document_retention" in rule_ids


# ---------------------------------------------------------------------------
# Stage 3: Inference graph
# ---------------------------------------------------------------------------


class TestBuildInferenceGraph:

    def test_each_rule_produces_one_node(self):
        engine = _make_engine()
        facts = [LegalFact("f1", "Email was sent.", "fact", "s1", 1.0)]
        rules = engine._map_rules(facts)
        nodes = engine._build_inference_graph(rules, facts)
        assert len(nodes) == len(rules)

    def test_confidence_capped_by_min_of_rule_and_fact_weight(self):
        engine = _make_engine()
        # hypothesis weight=0.4 should cap the node confidence below rule.base_confidence
        facts = [LegalFact("f1", "Message was sent.", "hypothesis", "s1", 0.4)]
        rules = engine._map_rules(facts)
        nodes = engine._build_inference_graph(rules, facts)
        for node in nodes:
            if "rule_workplace_comms" in node.premise_rule_ids:
                # rule.base_confidence=0.75; fact weight=0.4 → min(0.75, 0.4) = 0.4
                assert node.confidence <= 0.4

    def test_low_confidence_flag_set_when_confidence_below_05(self):
        engine = _make_engine()
        facts = [
            LegalFact("f1", "Termination following complaint.", "hypothesis", "s1", 0.3)
        ]
        rules = engine._map_rules(facts)
        nodes = engine._build_inference_graph(rules, facts)
        for node in nodes:
            if node.confidence < 0.5:
                assert "low_confidence" in node.uncertainty_reason

    def test_empty_rules_produces_empty_nodes(self):
        engine = _make_engine()
        nodes = engine._build_inference_graph([], [])
        assert nodes == []

    def test_node_ids_are_unique(self):
        engine = _make_engine()
        facts = [
            LegalFact("f1", "Email sent.", "fact", "s1", 1.0),
            LegalFact("f2", "Document retained.", "fact", "s2", 1.0),
        ]
        rules = engine._map_rules(facts)
        nodes = engine._build_inference_graph(rules, facts)
        ids = [n.node_id for n in nodes]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Full-pipeline properties
# ---------------------------------------------------------------------------


class TestFullPipeline:

    def _full_ir(self) -> CanonicalSemanticIR:
        return _make_ir(
            (
                FactStatement(
                    content="An email was sent to HR about the complaint.",
                    source_id="s1",
                ),
                EvidenceInterpretationStatement(
                    evidence_reference="ev-001",
                    interpretation="The timeline of events may indicate proximity between complaint and termination.",
                    bounds="may_indicate",
                ),
                HypothesisStatement(
                    claim="The document was deleted after the complaint was filed."
                ),
            )
        )

    def test_analyze_returns_legal_analysis_output(self):
        engine = _make_engine()
        result = _analyze_governed(engine, self._full_ir(), case_id="case-001")
        assert isinstance(result, LegalAnalysisOutput)

    def test_prohibited_conclusions_always_empty_tuple(self):
        engine = _make_engine()
        result = _analyze_governed(engine, self._full_ir(), case_id="case-001")
        assert result.prohibited_conclusions == ()

    def test_reasoning_version_is_non_empty(self):
        engine = _make_engine()
        result = _analyze_governed(engine, self._full_ir(), case_id="case-001")
        assert result.reasoning_version != ""

    def test_confidence_scores_all_in_bounds(self):
        engine = _make_engine()
        result = _analyze_governed(engine, self._full_ir(), case_id="case-001")
        for key, score in result.confidence_scores.items():
            assert 0.0 <= score <= 1.0, f"Confidence for {key!r} out of bounds: {score}"

    def test_invalid_composed_output_is_rejected(self, monkeypatch):
        engine = _make_engine()
        invalid_output = LegalAnalysisOutput(
            case_id="case-001",
            issues_identified=(),
            applicable_rules=(),
            supporting_evidence_ids=(),
            inference_chain=(),
            confidence_scores={},
            uncertainty_flags=(),
            reasoning_version="v1.0:v1.0",
            agent_version=_AGENT_VERSION,
            prohibited_conclusions=("liability",),  # type: ignore[arg-type]
        )

        monkeypatch.setattr(
            engine,
            "_compose_output",
            lambda case_id, nodes, rules, facts: invalid_output,
        )

        with pytest.raises(
            LegalReasoningError, match="Legal reasoning pipeline failed"
        ):
            _analyze_governed(engine, self._full_ir(), case_id="case-001")

    def test_determinism(self):
        """Same IR + same engine version must produce identical output."""
        engine1 = _make_engine()
        engine2 = _make_engine()
        ir = self._full_ir()
        result1 = _analyze_governed(engine1, ir, case_id="case-002")
        result2 = _analyze_governed(engine2, ir, case_id="case-002")
        assert result1 == result2

    def test_case_id_preserved(self):
        engine = _make_engine()
        result = _analyze_governed(engine, self._full_ir(), case_id="case-XYZ")
        assert result.case_id == "case-XYZ"

    def test_empty_ir_succeeds_with_empty_output(self):
        engine = _make_engine()
        result = _analyze_governed(engine, _make_ir(()), case_id="case-empty")
        assert result.issues_identified == ()
        assert result.applicable_rules == ()
        assert result.inference_chain == ()
        assert result.prohibited_conclusions == ()

    def test_classification_only_ir_produces_no_facts_and_no_rules(self):
        """ClassificationStatement-only IR must not generate any legal issues."""
        engine = _make_engine()
        ir = _make_ir(
            (ClassificationStatement(classification="urgent", confidence=0.99),)
        )
        result = _analyze_governed(engine, ir, case_id="case-cls-only")
        assert result.issues_identified == ()
        assert result.applicable_rules == ()

    def test_fail_closed_on_invalid_registry(self):
        """If registry raises unexpectedly, engine must raise LegalReasoningError."""

        class BrokenRegistry:
            def find_applicable(self, facts):
                raise RuntimeError("registry unavailable")

        engine = LegalReasoningEngine(
            rule_registry=BrokenRegistry(),
            agent_version=_AGENT_VERSION,
        )
        with pytest.raises(LegalReasoningError):
            _analyze_governed(engine, self._full_ir(), case_id="case-broken")
