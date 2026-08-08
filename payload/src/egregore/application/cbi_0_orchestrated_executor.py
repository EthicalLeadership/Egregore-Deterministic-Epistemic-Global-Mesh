from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from egregore.application.cbi_0_binding_audit_emitter import MemoryBindingAuditEmitter
from egregore.application.cbi_0_composition_guard import CompositionGuard
from egregore.application.cbi_0_projection_access_monitor import (
    ProjectionAccessMonitor,
)
from egregore.application.cbi_0_projection_registry_validator import (
    ProjectionRegistryValidator,
)
from egregore.application.legal_reasoning_engine import LegalReasoningEngine
from egregore.domain.legal_agent.execution_authority import ExecutionAuthority
from egregore.domain.legal_agent.legal_models import LegalAnalysisOutput
from egregore.domain.semantics.canonical_ir import (
    CanonicalSemanticIR,
    ClassificationStatement,
    EvidenceInterpretationStatement,
    FactStatement,
    HypothesisStatement,
)
from egregore.domain.semantics.projection_descriptor import (
    IRField,
    ProjectionDescriptor,
)
from egregore.interface.constraint_binding_ports import IBindingAuditEmitter
from egregore.shared.canonical import canonical_json

LEGAL_AGENT_ID = "legal_agent"
LEGAL_AGENT_VERSION = "v1.0"


def infer_accessed_ir_fields_for_legal_agent_v1(
    ir: CanonicalSemanticIR,
) -> frozenset[IRField]:
    """
    Deterministic access inference for Legal Agent v1.

    This is not reflection/bytecode tracing; it is a semantic, code-aligned inference
    derived from the current LegalReasoningEngine._bind_facts logic:
    - if the agent iterates statements, it performs statement type discrimination
    - Fact/Evidence/Hypothesis statements cause it to read ATTRIBUTE-bearing fields
    - EvidenceInterpretationStatement causes it to read evidence-bounded content (EVIDENCE_BLOCK)
    """
    if not ir.statements:
        return frozenset()

    accessed: set[IRField] = {IRField.ENTITY_TYPE}

    saw_attribute = False
    saw_evidence_block = False

    for stmt in ir.statements:
        if isinstance(stmt, FactStatement):
            saw_attribute = True
        elif isinstance(stmt, EvidenceInterpretationStatement):
            saw_attribute = True
            saw_evidence_block = True
        elif isinstance(stmt, HypothesisStatement):
            saw_attribute = True
        elif isinstance(stmt, ClassificationStatement):
            # Classification is excluded from legal facts in _bind_facts; no attribute reads.
            pass

    if saw_attribute:
        accessed.add(IRField.ATTRIBUTE)
    if saw_evidence_block:
        accessed.add(IRField.EVIDENCE_BLOCK)

    return frozenset(accessed)


@dataclass(frozen=True)
class CBI0OrchestratedResult:
    output: LegalAnalysisOutput
    m4_record: object  # BindingAuditRecord (kept loose to avoid circular imports)


def enforce_cbi0_runtime_chain_for_legal_ir(
    *,
    ir: CanonicalSemanticIR,
    descriptors: Mapping[tuple[str, str], ProjectionDescriptor],
    overlap_classifications: list[object],
    runtime_label: str,
    monitor: ProjectionAccessMonitor | None = None,
    registry_validator: ProjectionRegistryValidator | None = None,
    audit_emitter: MemoryBindingAuditEmitter | None = None,
) -> object:
    """Run the mandatory CBI-0 governance checkpoint over canonical IR.

    This helper enforces the non-bypass governance chain segments that are applicable
    before agent output exists:
    - M2: registry admission validation
    - M1: projection access scope validation
    - M4: deterministic audit emission via equivalence sweep

    M3 (terminal output composition guard) is enforced at the point where an agent
    output artifact exists; see CBI0OrchestratedExecutor.run_legal_agent_v1.
    """
    m1_monitor = monitor or ProjectionAccessMonitor()
    m2_validator = registry_validator or ProjectionRegistryValidator()
    m4_emitter = audit_emitter or MemoryBindingAuditEmitter()

    m2_validator.validate_registry(
        descriptors=dict(descriptors),
        overlap_classifications=overlap_classifications,
        active_agent_ids=[(LEGAL_AGENT_ID, LEGAL_AGENT_VERSION)],
    )

    descriptor = descriptors[(LEGAL_AGENT_ID, LEGAL_AGENT_VERSION)]
    m1_monitor.declare(
        agent_id=LEGAL_AGENT_ID, version=LEGAL_AGENT_VERSION, descriptor=descriptor
    )

    accessed_fields = ir.m1_accessed_fields
    if accessed_fields is None:
        accessed_fields = infer_accessed_ir_fields_for_legal_agent_v1(ir)

    m1_monitor.validate_access(
        agent_id=LEGAL_AGENT_ID,
        version=LEGAL_AGENT_VERSION,
        accessed_fields=accessed_fields,
    )

    sorted(
        (
            agent_id,
            version,
            desc.canonical_hash(),
        )
        for (agent_id, version), desc in m1_monitor.snapshot_declared().items()
    )
    sorted(
        (
            agent_id,
            version,
            desc.canonical_hash(),
        )
        for (agent_id, version), desc in descriptors.items()
    )
    # Adversarially meaningful M4 must not echo declared/registry descriptor commitments
    # into runtime_state_repr, otherwise equivalence becomes non-adversarial.
    runtime_state_repr = canonical_json(
        {
            "runtime_label": runtime_label,
            "agent": {
                "id": LEGAL_AGENT_ID,
                "version": LEGAL_AGENT_VERSION,
            },
            "accessed_fields": sorted(field.value for field in accessed_fields),
        }
    )
    return m4_emitter.emit_equivalence_sweep(
        descriptors=dict(descriptors),
        runtime_state_repr=runtime_state_repr,
    )


class CBI0OrchestratedExecutor:
    """
    Minimal “Layer 3” orchestration runtime that actually wires CBI-0 M1–M4
    around a single domain agent.

    This is the first concrete activation step toward:
    Admission → Projection validation → Agent execution monitor → Composition guard → Audit emission

    It is intentionally single-agent for now; ACL-0/ACL-1 multi-agent composition
    is out of scope.
    """

    def __init__(
        self,
        *,
        monitor: ProjectionAccessMonitor | None = None,
        registry_validator: ProjectionRegistryValidator | None = None,
        composition_guard: CompositionGuard | None = None,
        audit_emitter: MemoryBindingAuditEmitter | None = None,
    ) -> None:
        self._monitor = monitor or ProjectionAccessMonitor()
        self._registry_validator = registry_validator or ProjectionRegistryValidator()
        self._guard = composition_guard or CompositionGuard()
        self._emitter: IBindingAuditEmitter = (
            audit_emitter or MemoryBindingAuditEmitter()
        )

    def run_legal_agent_v1(
        self,
        *,
        agent_id: str,
        version: str,
        ir: CanonicalSemanticIR,
        case_id: str,
        legal_engine: LegalReasoningEngine,
        descriptors: Mapping[tuple[str, str], ProjectionDescriptor],
        overlap_classifications: list[object],
    ) -> CBI0OrchestratedResult:
        if (agent_id, version) != (LEGAL_AGENT_ID, LEGAL_AGENT_VERSION):
            raise ValueError(
                "CBI0OrchestratedExecutor currently supports only legal_agent v1.0; "
                f"got {(agent_id, version)!r}"
            )

        m4_record = enforce_cbi0_runtime_chain_for_legal_ir(
            ir=ir,
            descriptors=descriptors,
            overlap_classifications=overlap_classifications,
            runtime_label="live",
            monitor=self._monitor,
            registry_validator=self._registry_validator,
            audit_emitter=self._emitter,
        )

        # Domain agent execution (does not modify BIOK)
        # Sovereignty rule: analysis may only run inside the governed scope.
        with ExecutionAuthority.governed():
            output = legal_engine.analyze(ir, case_id=case_id)

        # M3 terminal output non-reentry (fail-closed)
        self._guard.assert_terminal(output=output, source_agent_id=agent_id)

        return CBI0OrchestratedResult(output=output, m4_record=m4_record)
