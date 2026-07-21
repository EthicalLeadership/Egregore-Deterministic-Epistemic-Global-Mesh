from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from egregore.domain.semantics.projection_descriptor import IRField


class SemanticStatementType(StrEnum):
    """Typed semantic lattice: what CAN be expressed in the IR."""

    FACT = "fact"
    CLASSIFICATION = "classification"
    EVIDENCE_INTERPRETATION = "evidence_interpretation"
    HYPOTHESIS = "hypothesis"
    # NOTE: LegalConclusion is deliberately ABSENT from this enum.
    # ProhibitedLegalClaim cannot be represented in this IR.


@dataclass(frozen=True)
class FactStatement:
    """Immutable representation of verifiable facts."""

    statement_type: SemanticStatementType = SemanticStatementType.FACT
    content: str = ""
    source_id: str = ""


@dataclass(frozen=True)
class ClassificationStatement:
    """Immutable representation of system routing/classification decisions."""

    statement_type: SemanticStatementType = SemanticStatementType.CLASSIFICATION
    classification: str = ""
    confidence: float = 0.0


@dataclass(frozen=True)
class EvidenceInterpretationStatement:
    """Bounded interpretation: "may indicate", "could support if...", never legal determination."""

    statement_type: SemanticStatementType = (
        SemanticStatementType.EVIDENCE_INTERPRETATION
    )
    evidence_reference: str = ""
    interpretation: str = ""
    bounds: str = ""  # e.g., "may_indicate", "could_support_if_combined"


@dataclass(frozen=True)
class HypothesisStatement:
    """Speculative statement subject to external verification."""

    statement_type: SemanticStatementType = SemanticStatementType.HYPOTHESIS
    claim: str = ""
    supporting_evidence_ids: tuple[str, ...] = ()


# The union type: only these four statement types can exist in the IR.
SemanticStatement = (
    FactStatement
    | ClassificationStatement
    | EvidenceInterpretationStatement
    | HypothesisStatement
)


@dataclass(frozen=True)
class CanonicalSemanticIR:
    """
    Canonical Intermediate Representation for semantic outputs.

    This IR makes forbidden semantic states (legal conclusions) structurally
    unrepresentable. The type system prevents construction of invalid states.
    """

    version_id: str
    reasoning_version_id: str
    statements: tuple[SemanticStatement, ...]

    # Optional governance hint:
    # When populated by the representation boundary (deserialize_to_canonical_ir),
    # it avoids a second IR walk for M1 access inference during CBI-0 enforcement.
    m1_accessed_fields: frozenset[IRField] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "reasoning_version_id": self.reasoning_version_id,
            "statements": [self._serialize_statement(s) for s in self.statements],
            "reasoning_guard_invariant": (
                "No amount of correlated metadata is sufficient to convert system routing outputs "
                "into legal determinations. ProhibitedLegalClaim is structurally unrepresentable in this IR."
            ),
        }

    @staticmethod
    def _serialize_statement(stmt: SemanticStatement) -> dict[str, Any]:
        if isinstance(stmt, FactStatement):
            return {
                "type": "fact",
                "content": stmt.content,
                "source_id": stmt.source_id,
            }
        elif isinstance(stmt, ClassificationStatement):
            return {
                "type": "classification",
                "classification": stmt.classification,
                "confidence": stmt.confidence,
            }
        elif isinstance(stmt, EvidenceInterpretationStatement):
            return {
                "type": "evidence_interpretation",
                "evidence_reference": stmt.evidence_reference,
                "interpretation": stmt.interpretation,
                "bounds": stmt.bounds,
            }
        elif isinstance(stmt, HypothesisStatement):
            return {
                "type": "hypothesis",
                "claim": stmt.claim,
                "supporting_evidence_ids": stmt.supporting_evidence_ids,
            }
        else:
            raise ValueError(f"Unknown statement type: {type(stmt)}")


def validate_semantic_ir_contract(ir: CanonicalSemanticIR) -> None:
    """
    Validate that IR conforms to the canonical contract.
    This is a defensive check; the type system already prevents invalid IR construction.
    """
    if not ir.version_id:
        raise ValueError("IR version_id is required")
    if not ir.reasoning_version_id:
        raise ValueError("Reasoning version_id is required")

    for i, stmt in enumerate(ir.statements):
        if stmt.statement_type not in {s.value for s in SemanticStatementType}:
            raise ValueError(f"Statement {i} has invalid type: {stmt.statement_type}")

        # Structural invariant: these statement types are the only allowed ones
        if not isinstance(
            stmt,
            (
                FactStatement,
                ClassificationStatement,
                EvidenceInterpretationStatement,
                HypothesisStatement,
            ),
        ):
            raise ValueError(
                f"Statement {i} is not a recognized semantic statement type"
            )
