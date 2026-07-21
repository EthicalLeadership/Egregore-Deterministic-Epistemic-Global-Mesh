import pytest

from egregore.domain.semantics.canonical_ir import (
    CanonicalSemanticIR,
    ClassificationStatement,
    EvidenceInterpretationStatement,
    FactStatement,
    HypothesisStatement,
    SemanticStatementType,
    validate_semantic_ir_contract,
)
from egregore.domain.semantics.ir_deserialization import (
    CanonicalIRDeserializationError,
    deserialize_to_canonical_ir,
)


class TestCanonicalIRStructure:
    """Test that canonical IR prevents forbidden semantic states from being representable."""

    def test_fact_statement_is_representable(self) -> None:
        fact = FactStatement(content="Found email with timestamp", source_id="doc_123")
        assert fact.statement_type == SemanticStatementType.FACT
        assert fact.content == "Found email with timestamp"

    def test_classification_statement_is_representable(self) -> None:
        classification = ClassificationStatement(
            classification="route_to_legal_review",
            confidence=0.85,
        )
        assert classification.statement_type == SemanticStatementType.CLASSIFICATION
        assert classification.confidence == 0.85

    def test_evidence_interpretation_is_representable(self) -> None:
        """Evidence interpretation is bounded: only 'may indicate' form, never legal conclusions."""
        interp = EvidenceInterpretationStatement(
            evidence_reference="doc_123",
            interpretation="Timeline could indicate sequence of events",
            bounds="may_indicate",
        )
        assert interp.statement_type == SemanticStatementType.EVIDENCE_INTERPRETATION
        assert "could indicate" in interp.interpretation

    def test_hypothesis_is_representable(self) -> None:
        hypothesis = HypothesisStatement(
            claim="User action X may have preceded event Y",
            supporting_evidence_ids=("doc_1", "doc_2"),
        )
        assert hypothesis.statement_type == SemanticStatementType.HYPOTHESIS
        assert len(hypothesis.supporting_evidence_ids) == 2

    def test_canonical_ir_serialization(self) -> None:
        """IR serialization includes reasoning invariant."""
        ir = CanonicalSemanticIR(
            version_id="ir-v1-test",
            reasoning_version_id="reasoning-v1",
            statements=(
                FactStatement(content="Found evidence", source_id="s1"),
                ClassificationStatement(classification="route", confidence=0.9),
            ),
        )
        serialized = ir.to_dict()
        assert "reasoning_guard_invariant" in serialized
        assert (
            "No amount of correlated metadata"
            in serialized["reasoning_guard_invariant"]
        )
        assert len(serialized["statements"]) == 2

    def test_forbidden_legal_conclusion_unrepresentable(self) -> None:
        """Legal conclusions cannot be constructed in the type system."""
        # This test documents that ProhibitedLegalClaim is not in the union type.
        # The type system makes it structurally impossible to create invalid semantic states.
        # If someone tries to construct an invalid statement, it should be caught at deserialization.
        valid_statements = (
            FactStatement(content="email received", source_id="1"),
            ClassificationStatement(classification="route", confidence=0.8),
        )
        ir = CanonicalSemanticIR(
            version_id="ir-v1-test",
            reasoning_version_id="reasoning-v1",
            statements=valid_statements,
        )
        # This IR contains only representable semantic statements
        assert len(ir.statements) == 2


class TestCanonicalIRDeserialization:
    """Test that deserialization boundary rejects forbidden semantic fields."""

    def test_deserialize_clean_fact_layer(self) -> None:
        untrusted = {
            "fact_layer": {
                "m1": "Email sent 2024-01-15",
            },
            "classification_layer": {},
            "interpretation_layer": {},
        }
        ir = deserialize_to_canonical_ir(
            untrusted_payload=untrusted,
            version_id="ir-v1-test",
            reasoning_version_id="reasoning-v1",
        )
        assert len(ir.statements) >= 1
        facts = [s for s in ir.statements if isinstance(s, FactStatement)]
        assert len(facts) == 1

    def test_deserialize_rejects_forbidden_legal_conclusions_key(self) -> None:
        """Deserialization boundary rejects legal_conclusions at top level."""
        untrusted = {
            "fact_layer": {},
            "legal_conclusions": ["This proves liability"],  # FORBIDDEN
        }
        with pytest.raises(CanonicalIRDeserializationError) as excinfo:
            deserialize_to_canonical_ir(
                untrusted_payload=untrusted,
                version_id="ir-v1-test",
                reasoning_version_id="reasoning-v1",
            )
        assert "Forbidden field" in str(excinfo.value)
        assert "legal_conclusions" in str(excinfo.value)

    def test_deserialize_rejects_liability_key(self) -> None:
        untrusted = {
            "fact_layer": {},
            "liability": "Confirmed",  # FORBIDDEN
        }
        with pytest.raises(CanonicalIRDeserializationError):
            deserialize_to_canonical_ir(
                untrusted_payload=untrusted,
                version_id="ir-v1-test",
                reasoning_version_id="reasoning-v1",
            )

    def test_deserialize_rejects_wrongdoing_confirmed_key(self) -> None:
        untrusted = {
            "fact_layer": {},
            "wrongdoing_confirmed": True,  # FORBIDDEN
        }
        with pytest.raises(CanonicalIRDeserializationError):
            deserialize_to_canonical_ir(
                untrusted_payload=untrusted,
                version_id="ir-v1-test",
                reasoning_version_id="reasoning-v1",
            )

    def test_deserialize_rejects_forbidden_interpretation_language(self) -> None:
        """Evidence interpretation layer rejects forbidden legal-conclusion phrasing."""
        untrusted = {
            "fact_layer": {},
            "classification_layer": {},
            "interpretation_layer": {
                "statements": [
                    "This establishes liability for wrongdoing"  # FORBIDDEN PHRASE
                ]
            },
        }
        with pytest.raises(CanonicalIRDeserializationError) as excinfo:
            deserialize_to_canonical_ir(
                untrusted_payload=untrusted,
                version_id="ir-v1-test",
                reasoning_version_id="reasoning-v1",
            )
        assert "Forbidden interpretation statement" in str(excinfo.value)

    def test_deserialize_allows_evidence_bounded_interpretation(self) -> None:
        """Evidence interpretation with 'may indicate' phrasing is allowed."""
        untrusted = {
            "fact_layer": {
                "d1": "Timeline shows activity at 15:30",
            },
            "classification_layer": {
                "routing": "needs_review",
            },
            "interpretation_layer": {
                "statements": [
                    "Timeline could indicate sequence of events if corroborated by witness",
                ]
            },
        }
        ir = deserialize_to_canonical_ir(
            untrusted_payload=untrusted,
            version_id="ir-v1-test",
            reasoning_version_id="reasoning-v1",
        )
        assert ir is not None
        interps = [
            s for s in ir.statements if isinstance(s, EvidenceInterpretationStatement)
        ]
        assert len(interps) >= 1

    def test_deserialize_preserves_version_ids(self) -> None:
        """Version IDs are preserved for replay determinism."""
        untrusted = {"fact_layer": {}}
        ir = deserialize_to_canonical_ir(
            untrusted_payload=untrusted,
            version_id="ir-v1-custom",
            reasoning_version_id="reasoning-v2-beta",
        )
        assert ir.version_id == "ir-v1-custom"
        assert ir.reasoning_version_id == "reasoning-v2-beta"


class TestCanonicalIRContract:
    """Test IR contract validation."""

    def test_validate_valid_ir_contract(self) -> None:
        ir = CanonicalSemanticIR(
            version_id="ir-v1",
            reasoning_version_id="reasoning-v1",
            statements=(FactStatement(content="test", source_id="1"),),
        )
        # Should not raise
        validate_semantic_ir_contract(ir)

    def test_validate_rejects_missing_version_id(self) -> None:
        ir = CanonicalSemanticIR(
            version_id="",  # INVALID
            reasoning_version_id="reasoning-v1",
            statements=(),
        )
        with pytest.raises(ValueError) as excinfo:
            validate_semantic_ir_contract(ir)
        assert "version_id" in str(excinfo.value)

    def test_validate_rejects_missing_reasoning_version_id(self) -> None:
        ir = CanonicalSemanticIR(
            version_id="ir-v1",
            reasoning_version_id="",  # INVALID
            statements=(),
        )
        with pytest.raises(ValueError) as excinfo:
            validate_semantic_ir_contract(ir)
        assert "Reasoning version_id" in str(excinfo.value)
