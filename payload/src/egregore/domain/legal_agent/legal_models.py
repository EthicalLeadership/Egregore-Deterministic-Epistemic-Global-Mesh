from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LegalFact:
    """Normalized legal primitive projected from CanonicalSemanticIR.

    Terminal domain object: does not re-enter BIOK boundary without re-validation.
    """

    fact_id: str
    content: str
    source_statement_type: str  # "fact" | "evidence_interpretation" | "hypothesis"
    source_id: str
    confidence_weight: float = 1.0


@dataclass(frozen=True)
class RuleMatch:
    """A legal rule matched to one or more extracted facts.

    Terminal domain object.
    """

    rule_id: str
    rule_text: str
    jurisdiction: str
    matched_fact_ids: tuple[str, ...]
    confidence: float  # [0.0, 1.0]


@dataclass(frozen=True)
class InferenceNode:
    """One reasoning step in the inference graph.

    Terminal domain object. Conclusions are evidence-bounded interpretations only;
    legal conclusions are prohibited by BIOK boundary validation.
    """

    node_id: str
    premise_rule_ids: tuple[str, ...]
    premise_fact_ids: tuple[str, ...]
    conclusion: str
    confidence: float  # [0.0, 1.0]
    uncertainty_reason: str  # empty string if none


@dataclass(frozen=True)
class LegalAgentVersion:
    """Version envelope for replay pinning and audit traceability."""

    rule_registry_version: str
    inference_engine_version: str


@dataclass(frozen=True)
class LegalAnalysisOutput:
    """Structured output of the legal reasoning morphism IR → LegalAnalysisOutput.

    Invariants (enforced by BIOK boundary validation):
    - prohibited_conclusions is always ()   (structurally typed empty tuple)
    - confidence_scores values are in [0.0, 1.0]
    - reasoning_version is non-empty
    - no forbidden language in inference_chain.conclusion or issues_identified

    Terminal domain object: must pass BIOK validate_legal_analysis_output()
    before re-entering any system boundary.
    """

    case_id: str
    issues_identified: tuple[str, ...]
    applicable_rules: tuple[RuleMatch, ...]
    supporting_evidence_ids: tuple[str, ...]
    inference_chain: tuple[InferenceNode, ...]
    confidence_scores: dict[str, float]
    uncertainty_flags: tuple[str, ...]
    reasoning_version: str
    agent_version: LegalAgentVersion
    prohibited_conclusions: tuple[
        ()
    ] = ()  # structurally typed empty — cannot be a legal conclusion
