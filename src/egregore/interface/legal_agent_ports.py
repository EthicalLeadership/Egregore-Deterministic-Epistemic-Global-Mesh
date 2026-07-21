from __future__ import annotations

from typing import Any, Protocol

from egregore.domain.legal_agent.legal_models import (
    LegalAnalysisOutput,
    LegalFact,
    RuleMatch,
)


class IRuleRegistry(Protocol):
    """Port: legal rule knowledge source.

    Implementations can be static (Phase 1), file-backed, database-driven, or ML-based.
    The LegalReasoningEngine depends only on this Protocol — never on a concrete registry.
    """

    def find_applicable(self, facts: list[LegalFact]) -> list[RuleMatch]: ...


class ILegalAgent(Protocol):
    """Port: BIOK→Agent input boundary.

    Contract:
    - ir argument is immutable (CanonicalSemanticIR is a frozen dataclass)
    - agent cannot modify IR or inject state back into BIOK
    - output must be a LegalAnalysisOutput that passes validate_legal_analysis_output()
    """

    def analyze(self, ir: Any, case_id: str) -> LegalAnalysisOutput: ...


class LegalOutputBoundaryError(Exception):
    """Raised when legal agent output violates BIOK structural constraints.

    The BIOK boundary validates structural correctness only — not reasoning quality.
    """


_FORBIDDEN_CONCLUSION_PHRASES: tuple[str, ...] = (
    "establishes liability",
    "proves wrongdoing",
    "confirmed retaliation",
    "confirmed violation",
    "legally sufficient",
    "legal conclusion",
)


def validate_legal_analysis_output(output: LegalAnalysisOutput) -> None:  # noqa: C901
    """BIOK-side structural boundary check on legal agent output.

    Called after agent returns output and before output enters any system boundary.
    Raises LegalOutputBoundaryError on any violation.

    Validates:
    1. prohibited_conclusions is always empty (structural invariant)
    2. confidence_scores values are in [0.0, 1.0]
    3. reasoning_version is non-empty (required for audit traceability)
    4. inference_chain conclusions contain no forbidden legal-conclusion language
    5. issues_identified contains no forbidden legal-conclusion language
    """
    if output.prohibited_conclusions != ():
        raise LegalOutputBoundaryError(
            "prohibited_conclusions must always be () — legal conclusions are structurally prohibited"
        )

    for key, score in output.confidence_scores.items():
        if not isinstance(score, (int, float)) or not (0.0 <= float(score) <= 1.0):
            raise LegalOutputBoundaryError(
                f"Confidence score for '{key}' is out of bounds: {score} (must be in [0.0, 1.0])"
            )

    if not output.reasoning_version:
        raise LegalOutputBoundaryError(
            "reasoning_version is required for audit traceability"
        )

    for node in output.inference_chain:
        lower = node.conclusion.lower()
        for phrase in _FORBIDDEN_CONCLUSION_PHRASES:
            if phrase in lower:
                raise LegalOutputBoundaryError(
                    f"InferenceNode conclusion contains forbidden language: '{phrase}'"
                )

    for issue in output.issues_identified:
        lower = issue.lower()
        for phrase in _FORBIDDEN_CONCLUSION_PHRASES:
            if phrase in lower:
                raise LegalOutputBoundaryError(
                    f"issues_identified contains forbidden language: '{phrase}'"
                )
