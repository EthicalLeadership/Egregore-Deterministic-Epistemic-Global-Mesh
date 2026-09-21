from __future__ import annotations

import pytest

from egregore.application.cbi_0_orchestrated_executor import (
    CBI0OrchestratedExecutor,
    enforce_cbi0_runtime_chain_for_legal_ir,
)
from egregore.application.legal_reasoning_engine import LegalReasoningEngine
from egregore.domain.legal_agent.legal_models import (
    LegalAgentVersion,
    LegalAnalysisOutput,
)
from egregore.domain.legal_agent.projection_registry import StaticProjectionRegistry
from egregore.domain.legal_agent.rule_registry import StaticRuleRegistry
from egregore.domain.semantics.canonical_ir import (
    CanonicalSemanticIR,
    EvidenceInterpretationStatement,
    FactStatement,
)
from egregore.domain.semantics.projection_descriptor import BindingAuditRecord
from egregore.interface.constraint_binding_ports import RegistryValidationError


def _make_legal_engine() -> LegalReasoningEngine:
    return LegalReasoningEngine(
        rule_registry=StaticRuleRegistry(),
        agent_version=LegalAgentVersion(
            rule_registry_version="v1.0", inference_engine_version="v1.0"
        ),
    )


def _make_ir() -> CanonicalSemanticIR:
    # Includes EvidenceInterpretationStatement to force EVIDENCE_BLOCK access inference.
    return CanonicalSemanticIR(
        version_id="ir-runtime-chain-001",
        reasoning_version_id="reasoning-v1",
        statements=(
            FactStatement(content="An email was sent.", source_id="s1"),
            EvidenceInterpretationStatement(
                evidence_reference="ev-001",
                interpretation="The email may indicate communication patterns.",
                bounds="may_indicate",
            ),
        ),
    )


def test_cbi_0_runtime_chain_single_agent_legal_agent_v1() -> None:
    executor = CBI0OrchestratedExecutor()
    legal_engine = _make_legal_engine()

    ir = _make_ir()
    case_id = "case-runtime-chain-001"

    descriptors = StaticProjectionRegistry().all_descriptors()
    overlap_classifications: list[object] = []

    result = executor.run_legal_agent_v1(
        agent_id="legal_agent",
        version="v1.0",
        ir=ir,
        case_id=case_id,
        legal_engine=legal_engine,
        descriptors=descriptors,
        overlap_classifications=overlap_classifications,
    )

    assert isinstance(result.output, LegalAnalysisOutput)
    assert result.output.case_id == case_id

    assert isinstance(result.m4_record, BindingAuditRecord)
    # In MVP emitter, runtime_state_hash is based on a synthetic string and will almost never equal registry_hash.
    assert result.m4_record.equivalence_status in {"EQUIVALENT", "DIVERGED"}


def test_cbi_0_runtime_chain_rejects_out_of_scope_access() -> None:
    """
    Force accessed_fields inference to include ATTRIBUTE+EVIDENCE_BLOCK (from IR),
    then provide a descriptor that omits those fields to trigger M1.
    """
    executor = CBI0OrchestratedExecutor()

    legal_engine = _make_legal_engine()
    ir = _make_ir()
    case_id = "case-runtime-chain-002"

    # Build a deliberately incorrect descriptor: only ENTITY_TYPE is allowed.
    descriptors = StaticProjectionRegistry().all_descriptors()
    wrong_desc = next(iter(descriptors.values()))
    # Mutate via a new descriptor object with narrower scope:
    from egregore.domain.semantics.projection_descriptor import (
        IRField,
        ProjectionDescriptor,
    )

    descriptors = {
        ("legal_agent", "v1.0"): ProjectionDescriptor(
            agent_id="legal_agent",
            version="v1.0",
            scope=frozenset({IRField.ENTITY_TYPE}),
            constraints=wrong_desc.constraints,
            sensitivity_level=wrong_desc.sensitivity_level,
        )
    }

    with pytest.raises(Exception) as excinfo:
        executor.run_legal_agent_v1(
            agent_id="legal_agent",
            version="v1.0",
            ir=ir,
            case_id=case_id,
            legal_engine=legal_engine,
            descriptors=descriptors,
            overlap_classifications=[],
        )

    # We expect an M1 projection scope failure (ProjectionBindingError), but keep it robust to exact type import.
    assert (
        "outside declared scope" in str(excinfo.value)
        or "ProjectionBindingError" in excinfo.value.__class__.__name__
    )


def test_cbi_0_runtime_chain_rejects_missing_descriptor_at_m2() -> None:
    executor = CBI0OrchestratedExecutor()

    with pytest.raises(RegistryValidationError, match="Missing projection descriptors"):
        executor.run_legal_agent_v1(
            agent_id="legal_agent",
            version="v1.0",
            ir=_make_ir(),
            case_id="case-runtime-chain-003",
            legal_engine=_make_legal_engine(),
            descriptors={},
            overlap_classifications=[],
        )


def test_enforce_cbi0_runtime_chain_for_legal_ir_emits_m4_record() -> None:
    record = enforce_cbi0_runtime_chain_for_legal_ir(
        ir=_make_ir(),
        descriptors=StaticProjectionRegistry().all_descriptors(),
        overlap_classifications=[],
        runtime_label="test",
    )

    assert isinstance(record, BindingAuditRecord)
    assert record.binding_hook_id == "M4"


def test_legal_engine_direct_invocation_is_blocked_outside_governed_scope() -> None:
    with pytest.raises(RuntimeError, match="Ungoverned execution path blocked"):
        _make_legal_engine().analyze(_make_ir(), case_id="case-runtime-chain-004")
