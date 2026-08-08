from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from egregore.domain.semantics.canonical_ir import (
    CanonicalSemanticIR,
    ClassificationStatement,
    EvidenceInterpretationStatement,
    FactStatement,
    SemanticStatement,
    validate_semantic_ir_contract,
)
from egregore.domain.semantics.projection_descriptor import IRField

_FORBIDDEN_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"legal_conclusions", "legal_conclusion", "liability", "wrongdoing_confirmed"}
)

_FORBIDDEN_STATEMENT_KEYS: frozenset[str] = frozenset(
    {"legal_claim", "liability_determination", "wrongdoing_statement"}
)


class CanonicalIRDeserializationError(Exception):
    """Raised when untrusted input cannot be deserialized to canonical IR."""

    pass


def deserialize_to_canonical_ir(  # noqa: C901
    untrusted_payload: Mapping[str, Any],
    version_id: str,
    reasoning_version_id: str,
) -> CanonicalSemanticIR:
    """
    Deserialization boundary: convert untrusted input to canonical IR.

    This is the first canonical representation boundary.
    Forbidden semantic states are rejected here at the representation level.

    Raises CanonicalIRDeserializationError if input contains forbidden keys or invalid structure.
    """
    # Check for forbidden top-level keys before any further processing.
    for key in untrusted_payload:
        if key in _FORBIDDEN_TOP_LEVEL_KEYS:
            raise CanonicalIRDeserializationError(
                f"Forbidden field at top level: {key}. Legal conclusions cannot be represented in this IR."
            )

    statements: list[SemanticStatement] = []

    # Inline M1 access inference for Legal Agent v1:
    # matches cbi_0_orchestrated_executor.infer_accessed_ir_fields_for_legal_agent_v1()
    saw_attribute = False
    saw_evidence_block = False

    # Extract and validate fact layer
    fact_layer = untrusted_payload.get("fact_layer", {})
    if isinstance(fact_layer, dict):
        for key in fact_layer:
            if key in _FORBIDDEN_STATEMENT_KEYS:
                raise CanonicalIRDeserializationError(
                    f"Forbidden key in fact_layer: {key}"
                )

        for fact_id, fact_content in fact_layer.items():
            if isinstance(fact_content, str):
                statements.append(
                    FactStatement(
                        content=fact_content,
                        source_id=fact_id,
                    )
                )
                saw_attribute = True

    # Extract and validate classification layer
    classification_layer = untrusted_payload.get("classification_layer", {})
    if isinstance(classification_layer, dict):
        for key in classification_layer:
            if key in _FORBIDDEN_STATEMENT_KEYS:
                raise CanonicalIRDeserializationError(
                    f"Forbidden key in classification_layer: {key}"
                )

        routing = classification_layer.get("routing", "")
        if isinstance(routing, str):
            statements.append(
                ClassificationStatement(
                    classification=routing,
                    confidence=float(classification_layer.get("confidence", 0.5)),
                )
            )
            # NOTE: Classification is excluded from legal fact binding (_bind_facts) for M1.

    # Extract and validate interpretation layer (bounded to evidence interpretation only)
    interpretation_layer = untrusted_payload.get("interpretation_layer", {})
    if isinstance(interpretation_layer, dict):
        for key in interpretation_layer:
            if key in _FORBIDDEN_STATEMENT_KEYS:
                raise CanonicalIRDeserializationError(
                    f"Forbidden key in interpretation_layer: {key}"
                )

        interp_statements = interpretation_layer.get("statements", [])
        if isinstance(interp_statements, list):
            for stmt in interp_statements:
                if isinstance(stmt, str):
                    # All interpretation outputs must be evidence-bounded (not legal conclusions)
                    if _contains_forbidden_language(stmt):
                        raise CanonicalIRDeserializationError(
                            f"Forbidden interpretation statement: {stmt}. "
                            "Only evidence-bounded interpretation allowed."
                        )
                    statements.append(
                        EvidenceInterpretationStatement(
                            interpretation=stmt,
                            bounds="evidence_bounded",
                        )
                    )
                    saw_attribute = True
                    saw_evidence_block = True

    # Construct IR
    if not statements:
        m1_accessed_fields = frozenset()
    else:
        accessed = {IRField.ENTITY_TYPE}
        if saw_attribute:
            accessed.add(IRField.ATTRIBUTE)
        if saw_evidence_block:
            accessed.add(IRField.EVIDENCE_BLOCK)
        m1_accessed_fields = frozenset(accessed)

    ir = CanonicalSemanticIR(
        version_id=version_id,
        reasoning_version_id=reasoning_version_id,
        statements=tuple(statements),
        m1_accessed_fields=m1_accessed_fields,
    )

    # Validate contract
    try:
        validate_semantic_ir_contract(ir)
    except ValueError as exc:
        raise CanonicalIRDeserializationError(f"IR validation failed: {exc}") from exc

    return ir


def _contains_forbidden_language(text: str) -> bool:
    """
    Check if text contains forbidden legal-conclusion phrasing.
    This is a defensive check; structural IR design prevents this from reaching here.
    """
    forbidden = {
        "establishes liability",
        "proves wrongdoing",
        "legal conclusion",
        "legally sufficient",
        "confirmed retaliation",
        "confirmed violation",
    }
    lower = text.lower()
    return any(phrase in lower for phrase in forbidden)
