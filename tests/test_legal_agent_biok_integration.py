"""Legal Agent ↔ BIOK integration tests.

Validates the boundary between the BIOK semantic substrate and the Legal Agent domain:
1. IR immutability — agent cannot mutate the CanonicalSemanticIR it receives
2. Boundary validation — validate_legal_analysis_output() enforces BIOK constraints
3. Boundary isolation — agent failure does not corrupt BIOK state
4. Output re-entry guard — terminal outputs cannot silently re-enter BIOK as IR
"""

from __future__ import annotations

import pytest

from egregore.application.legal_reasoning_engine import (
    LegalReasoningEngine,
    LegalReasoningError,
)
from egregore.domain.legal_agent.execution_authority import ExecutionAuthority
from egregore.domain.legal_agent.legal_models import (
    InferenceNode,
    LegalAgentVersion,
    LegalAnalysisOutput,
    RuleMatch,
)
from egregore.domain.legal_agent.rule_registry import StaticRuleRegistry
from egregore.domain.semantics.canonical_ir import (
    CanonicalSemanticIR,
    FactStatement,
)
from egregore.interface.legal_agent_ports import (
    LegalOutputBoundaryError,
    validate_legal_analysis_output,
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
    # Integrations validate BIOK boundary while running inside governed scope.
    with ExecutionAuthority.governed():
        yield


def _make_engine() -> LegalReasoningEngine:
    return LegalReasoningEngine(
        rule_registry=StaticRuleRegistry(),
        agent_version=_AGENT_VERSION,
    )


def _make_ir(*extra_stmts) -> CanonicalSemanticIR:
    base = (
        FactStatement(content="Email was sent on the record date.", source_id="s1"),
    )
    return CanonicalSemanticIR(
        version_id="ir-integ-001",
        reasoning_version_id="rrid-integ-001",
        statements=base + tuple(extra_stmts),
    )


def _valid_output() -> LegalAnalysisOutput:
    rule = RuleMatch(
        rule_id="rule_workplace_comms",
        rule_text="Workplace comms may establish conduct patterns.",
        jurisdiction="general",
        matched_fact_ids=("s1",),
        confidence=0.75,
    )
    node = InferenceNode(
        node_id="node_0",
        premise_rule_ids=("rule_workplace_comms",),
        premise_fact_ids=("s1",),
        conclusion="Rule 'rule_workplace_comms' may apply based on facts ('s1',)",
        confidence=0.75,
        uncertainty_reason="",
    )
    return LegalAnalysisOutput(
        case_id="case-integ-001",
        issues_identified=("Potential issue under rule_workplace_comms (general)",),
        applicable_rules=(rule,),
        supporting_evidence_ids=("s1",),
        inference_chain=(node,),
        confidence_scores={"node_0": 0.75},
        uncertainty_flags=(),
        reasoning_version="v1.0:v1.0",
        agent_version=_AGENT_VERSION,
        prohibited_conclusions=(),
    )


# ---------------------------------------------------------------------------
# IR immutability
# ---------------------------------------------------------------------------


class TestIRImmutability:

    def test_ir_is_frozen_dataclass(self):
        ir = _make_ir()
        with pytest.raises((AttributeError, TypeError)):
            ir.version_id = "tampered"  # type: ignore[misc]

    def test_ir_statements_tuple_is_immutable(self):
        ir = _make_ir()
        with pytest.raises((AttributeError, TypeError)):
            ir.statements = ()  # type: ignore[misc]

    def test_agent_analysis_does_not_change_ir_version(self):
        engine = _make_engine()
        ir = _make_ir()
        original_version = ir.version_id
        engine.analyze(ir, case_id="case-freeze-001")
        assert ir.version_id == original_version

    def test_agent_analysis_does_not_change_ir_statements(self):
        engine = _make_engine()
        ir = _make_ir()
        original_stmts = ir.statements
        engine.analyze(ir, case_id="case-freeze-002")
        assert ir.statements == original_stmts


# ---------------------------------------------------------------------------
# Boundary validation: valid output passes
# ---------------------------------------------------------------------------


class TestBoundaryValidationValidOutput:

    def test_valid_output_passes_without_error(self):
        output = _valid_output()
        validate_legal_analysis_output(output)  # must not raise

    def test_engine_output_always_passes_boundary(self):
        """Engine-generated output must satisfy boundary validation unconditionally."""
        engine = _make_engine()
        ir = _make_ir()
        result = engine.analyze(ir, case_id="case-boundary-ok")
        validate_legal_analysis_output(result)  # must not raise


# ---------------------------------------------------------------------------
# Boundary validation: structural invariant violations
# ---------------------------------------------------------------------------


class TestBoundaryValidationStructural:

    def test_non_empty_prohibited_conclusions_rejected(self):
        """prohibited_conclusions must always be () — structural invariant."""
        output = _valid_output()
        # Construct a tampered output that bypasses the type — simulates a future
        # unsafe API or reflection-based injection attempt.
        import dataclasses

        tampered = dataclasses.replace(output, prohibited_conclusions=("liability",))  # type: ignore[arg-type]
        with pytest.raises(LegalOutputBoundaryError, match="prohibited_conclusions"):
            validate_legal_analysis_output(tampered)

    def test_confidence_score_above_1_rejected(self):
        import dataclasses

        bad_output = dataclasses.replace(
            _valid_output(),
            confidence_scores={"node_0": 1.1},
        )
        with pytest.raises(LegalOutputBoundaryError, match="out of bounds"):
            validate_legal_analysis_output(bad_output)

    def test_confidence_score_below_0_rejected(self):
        import dataclasses

        bad_output = dataclasses.replace(
            _valid_output(),
            confidence_scores={"node_0": -0.01},
        )
        with pytest.raises(LegalOutputBoundaryError, match="out of bounds"):
            validate_legal_analysis_output(bad_output)

    def test_empty_reasoning_version_rejected(self):
        import dataclasses

        bad_output = dataclasses.replace(_valid_output(), reasoning_version="")
        with pytest.raises(LegalOutputBoundaryError, match="reasoning_version"):
            validate_legal_analysis_output(bad_output)


# ---------------------------------------------------------------------------
# Boundary validation: forbidden language in conclusions
# ---------------------------------------------------------------------------


class TestBoundaryValidationForbiddenLanguage:

    def _output_with_conclusion(self, conclusion: str) -> LegalAnalysisOutput:
        import dataclasses

        node = InferenceNode(
            node_id="node_0",
            premise_rule_ids=("rule_workplace_comms",),
            premise_fact_ids=("s1",),
            conclusion=conclusion,
            confidence=0.75,
            uncertainty_reason="",
        )
        return dataclasses.replace(_valid_output(), inference_chain=(node,))

    def _output_with_issue(self, issue: str) -> LegalAnalysisOutput:
        import dataclasses

        return dataclasses.replace(_valid_output(), issues_identified=(issue,))

    def test_establishes_liability_in_conclusion_rejected(self):
        output = self._output_with_conclusion(
            "This establishes liability for the defendant."
        )
        with pytest.raises(LegalOutputBoundaryError, match="forbidden language"):
            validate_legal_analysis_output(output)

    def test_proves_wrongdoing_in_conclusion_rejected(self):
        output = self._output_with_conclusion(
            "The evidence proves wrongdoing occurred."
        )
        with pytest.raises(LegalOutputBoundaryError, match="forbidden language"):
            validate_legal_analysis_output(output)

    def test_confirmed_retaliation_in_issue_rejected(self):
        output = self._output_with_issue("confirmed retaliation against the claimant.")
        with pytest.raises(LegalOutputBoundaryError, match="forbidden language"):
            validate_legal_analysis_output(output)

    def test_legal_conclusion_phrase_in_issue_rejected(self):
        output = self._output_with_issue("This is a legal conclusion about the matter.")
        with pytest.raises(LegalOutputBoundaryError, match="forbidden language"):
            validate_legal_analysis_output(output)

    def test_safe_bounded_conclusion_passes(self):
        """Bounded language ('may apply', 'potential issue') must pass validation."""
        output = self._output_with_conclusion(
            "Rule 'rule_workplace_comms' may apply based on facts ('s1',)"
        )
        validate_legal_analysis_output(output)  # must not raise


# ---------------------------------------------------------------------------
# Boundary isolation: agent failure does not affect BIOK state
# ---------------------------------------------------------------------------


class TestBoundaryIsolation:

    def test_agent_failure_does_not_corrupt_ir(self):
        class BrokenRegistry:
            def find_applicable(self, facts):
                raise RuntimeError("catastrophic failure")

        engine = LegalReasoningEngine(
            rule_registry=BrokenRegistry(),
            agent_version=_AGENT_VERSION,
        )
        ir = _make_ir()
        original_stmts = ir.statements
        original_version = ir.version_id

        with pytest.raises(LegalReasoningError):
            engine.analyze(ir, case_id="case-isolate-001")

        # IR is intact after agent failure
        assert ir.statements == original_stmts
        assert ir.version_id == original_version

    def test_legal_output_cannot_be_directly_used_as_canonical_ir(self):
        """Terminal agent output has no CanonicalSemanticIR interface.

        This is a structural type check — LegalAnalysisOutput cannot satisfy the
        CanonicalSemanticIR shape without explicit re-validation. We verify it
        lacks the IR's required attributes.
        """
        output = _valid_output()
        assert not hasattr(output, "reasoning_version_id"), (
            "LegalAnalysisOutput must not carry reasoning_version_id — "
            "that field belongs to CanonicalSemanticIR only"
        )
        assert not hasattr(output, "statements"), (
            "LegalAnalysisOutput must not carry 'statements' — "
            "that field belongs to CanonicalSemanticIR only"
        )
