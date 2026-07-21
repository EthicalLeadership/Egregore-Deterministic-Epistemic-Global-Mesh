from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from egregore.application.cbi_0_composition_guard import CompositionGuard
from egregore.application.cbi_0_orchestrated_executor import (
    enforce_cbi0_runtime_chain_for_legal_ir,
)
from egregore.domain.legal_agent.projection_registry import StaticProjectionRegistry
from egregore.domain.semantics.derivations import derive_generate_artifacts
from egregore.domain.semantics.domain_adapters import DossierSemanticsDomainAdapter
from egregore.domain.semantics.ir_deserialization import (
    CanonicalIRDeserializationError,
    deserialize_to_canonical_ir,
)
from egregore.domain.semantics.observability import (
    EQUIV_O,
    PI_O,
    enforce_admissible_step,
    evaluator_metadata_rename_transform,
    validate_envelope_contract,
)
from egregore.domain.semantics.projection_descriptor import (
    OverlapClassification,
    ProjectionDescriptor,
)
from egregore.domain.semantics_models import (
    CaseState,
    CommandAck,
    GenerateDossierCommand,
    SemanticsError,
    StableErrorCode,
    TaskExecutionState,
)
from egregore.interface.constraint_binding_ports import (
    CompositionGuardError,
    ProjectionBindingError,
    RegistryValidationError,
)
from egregore.interface.semantics_ports import (
    IAuthzProvider,
    ICaseStore,
    IIdempotencyStore,
    ISemanticsDomainAdapter,
    ITransactionalPersistence,
)
from egregore.shared.canonical import canonical_json, sha256_hex

AllowedTransition = tuple[CaseState, CaseState]

_ALLOWED_TRANSITIONS: set[AllowedTransition] = {
    (CaseState.created, CaseState.active),
    (CaseState.active, CaseState.generating),
    (CaseState.generating, CaseState.versioned),
    (CaseState.versioned, CaseState.active),
    (CaseState.active, CaseState.archived),
    (CaseState.versioned, CaseState.archived),
}

_GENERATE_DOSSIER_EXECUTION_PATH: tuple[TaskExecutionState, ...] = (
    TaskExecutionState.INIT,
    TaskExecutionState.VALIDATE,
    TaskExecutionState.PLAN,
    TaskExecutionState.EXECUTE,
    TaskExecutionState.VERIFY,
    TaskExecutionState.COMMIT,
)


def derive_execution_id(command: GenerateDossierCommand) -> str:
    """
    CEIM-style execution_id used as the sole idempotency key in the core live path.

    Identity lattice (must be version-aware for invalidation):
    - organization_id
    - case_id
    - input_fingerprint
    - engine_version
    - policy_version
    - causality_id
    """
    payload = {
        "organization_id": command.organization_id,
        "case_id": command.case_id,
        "input_fingerprint": command.input_fingerprint,
        "engine_version": command.engine_version,
        "policy_version": command.policy_version,
        "causality_id": command.causality_id,
    }
    return sha256_hex(canonical_json(payload).encode("utf-8"))


def _validate_state_transition(*, current: CaseState, next_state: CaseState) -> None:
    if (current, next_state) not in _ALLOWED_TRANSITIONS:
        raise SemanticsError(
            code=StableErrorCode.FORBIDDEN_STATE_TRANSITION,
            message=f"Forbidden case transition {current.value} -> {next_state.value}",
        )


def _validate_execution_path(states: tuple[TaskExecutionState, ...]) -> None:
    if not states:
        raise SemanticsError(
            code=StableErrorCode.VALIDATION_FAILED,
            message="Execution path cannot be empty",
        )
    if states[0] != TaskExecutionState.INIT:
        raise SemanticsError(
            code=StableErrorCode.VALIDATION_FAILED,
            message="Execution path must start at INIT",
        )
    if states[-1] != TaskExecutionState.COMMIT:
        raise SemanticsError(
            code=StableErrorCode.VALIDATION_FAILED,
            message="Execution path must end at COMMIT",
        )


def _default_usage_deltas(*, organization_id: str) -> list[tuple[str, str, int]]:
    # Returns (organization_id, counter_name, delta).
    return [(organization_id, "dossier_generations", 1)]


@dataclass(frozen=True)
class GenerateDossierEngineResult:
    # Canonical dossier JSON-like structure.
    data: Mapping[str, Any]
    # Arbitrary metadata for response; must be deterministic.
    metadata: Mapping[str, Any]


class CorePlaneGenerateDossierExecutor:
    """
    Core Plane (deterministic kernel) command executor implementing Priority 1–4 semantics.

    Dependencies are injected as ports:
    - read-only case state store
    - idempotency result store
    - authz provider
    - transactional persistence (atomic T2 commit boundary)

    Determinism rule:
    - timestamp_ns is REQUIRED input for stable identity derivation.
      No wall-clock fallback is allowed in the Core Plane.

    Semantics collapse rule (non-negotiable):
    - Orchestration MUST NOT construct AuditEvent/OutboxEntry directly.
    - derive_generate_artifacts() is the single source of truth for artifacts.
    """

    def __init__(
        self,
        *,
        authz: IAuthzProvider,
        case_store: ICaseStore,
        idempotency_store: IIdempotencyStore,
        transactional_persistence: ITransactionalPersistence,
        compute_engine_policy: Callable[
            [GenerateDossierCommand], GenerateDossierEngineResult
        ],
        domain_adapter: ISemanticsDomainAdapter | None = None,
        projection_descriptors: (
            Mapping[tuple[str, str], ProjectionDescriptor] | None
        ) = None,
        overlap_classifications: list[OverlapClassification] | None = None,
    ) -> None:
        self._authz = authz
        self._case_store = case_store
        self._idempotency = idempotency_store
        self._tx = transactional_persistence
        self._compute = compute_engine_policy
        self._domain_adapter = domain_adapter or DossierSemanticsDomainAdapter()
        self._composition_guard = CompositionGuard()
        registry = StaticProjectionRegistry()
        self._projection_descriptors = dict(
            projection_descriptors
            if projection_descriptors is not None
            else registry.all_descriptors()
        )
        self._overlap_classifications = list(
            overlap_classifications
            if overlap_classifications is not None
            else registry.all_overlap_classifications()
        )

    def handle_generate_dossier(  # noqa: C901
        self,
        *,
        command: GenerateDossierCommand,
        timestamp_ns: int | None = None,
    ) -> CommandAck:
        """
        Deterministic lifecycle:

        - T1: idempotency lookup (no side effects)
        - AuthZ
        - Validate case state transition
        - Deterministic compute
        - Derive snapshot/events/outbox via domain derive_generate_artifacts()
        - T2: atomic durable commit (snapshot + audit events + outbox + usage + idempotency mapping)
        - Ack only after successful T2

        Fail-closed:
        - timestamp_ns missing is rejected
        - if T2 commit raises: no success response
        """
        if timestamp_ns is None:
            raise SemanticsError(
                code=StableErrorCode.VALIDATION_FAILED,
                message="timestamp_ns is required",
            )

        execution_id = derive_execution_id(command=command)

        # T1: idempotency lookup (no side effects)
        existing = self._idempotency.get_success_result(input_fingerprint=execution_id)
        if existing is not None:
            return CommandAck(
                http_status=200,
                result=existing,
                outbox_ids=None,
            )

        # Validation (minimal deterministic envelope checks)
        if not command.organization_id or not command.case_id or not command.actor_id:
            raise SemanticsError(
                code=StableErrorCode.VALIDATION_FAILED,
                message="Missing required fields",
            )

        # Authorization (fail-closed, before any durable mutation)
        self._authz.authorize_generate(command=command)

        current_state_raw = self._case_store.get_case_state(
            organization_id=command.organization_id, case_id=command.case_id
        )
        current_state = CaseState(current_state_raw)

        # Deterministic state machine (Phase 0 simplification)
        if current_state == CaseState.created:
            case_next_state = CaseState.active
        elif current_state == CaseState.active:
            case_next_state = CaseState.generating
        elif current_state == CaseState.generating:
            raise SemanticsError(
                code=StableErrorCode.FORBIDDEN_STATE_TRANSITION,
                message="Case already generating",
            )
        elif current_state == CaseState.versioned:
            raise SemanticsError(
                code=StableErrorCode.FORBIDDEN_STATE_TRANSITION,
                message="Case already versioned",
            )
        elif current_state == CaseState.archived:
            raise SemanticsError(
                code=StableErrorCode.FORBIDDEN_STATE_TRANSITION,
                message="Case is archived",
            )
        else:
            case_next_state = current_state

        _validate_state_transition(current=current_state, next_state=case_next_state)
        _validate_execution_path(_GENERATE_DOSSIER_EXECUTION_PATH)

        # Pure compute (deterministic)
        engine_out = self._compute(command)

        # CBI-0 M3 routing guard: fail closed if terminal legal output is routed
        # into CanonicalSemanticIR construction without an explicit bridge.
        try:
            self._composition_guard.assert_no_implicit_ir_synthesis(
                source_agent_id="legal_agent",
                target_input=engine_out.data,
                target_type_name="CanonicalSemanticIR",
            )
        except CompositionGuardError as exc:
            raise SemanticsError(
                code=StableErrorCode.VALIDATION_FAILED,
                message=f"CBI-0 composition guard failed: {exc}",
            ) from exc

        # CANONICAL IR BOUNDARY: convert untrusted compute output to typed IR
        # at the earliest representation boundary. Forbidden states are structurally unrepresentable.
        try:
            canonical_ir = deserialize_to_canonical_ir(
                untrusted_payload=engine_out.data,
                version_id=f"ir-v1-{command.engine_version}",
                reasoning_version_id="reasoning-v1",  # version-pinned for replay determinism
            )
        except CanonicalIRDeserializationError as exc:
            raise SemanticsError(
                code=StableErrorCode.VALIDATION_FAILED,
                message=f"Canonical IR deserialization failed: {exc}",
            ) from exc

        # CBI-0 governance checkpoint (M2/M1/M4) over canonical IR.
        # This makes governance non-optional for the live execution path.
        try:
            enforce_cbi0_runtime_chain_for_legal_ir(
                ir=canonical_ir,
                descriptors=self._projection_descriptors,
                overlap_classifications=self._overlap_classifications,
                runtime_label="live",
            )
        except (RegistryValidationError, ProjectionBindingError) as exc:
            raise SemanticsError(
                code=StableErrorCode.VALIDATION_FAILED,
                message=f"CBI-0 governance validation failed: {exc}",
            ) from exc

        task_contract = command.to_task_contract()
        execution_path = [s.value for s in _GENERATE_DOSSIER_EXECUTION_PATH]

        next_version_number = self._case_store.get_next_version_number(
            organization_id=command.organization_id, case_id=command.case_id
        )

        # Deterministic version_id is derived from a stable identity lattice:
        # for Phase 0 we keep the "version-placeholder" semantics stable.
        from egregore.shared.stable_ids import stable_event_id_from_components

        version_id = stable_event_id_from_components(
            organization_id=command.organization_id,
            case_id=command.case_id,
            version_id="version-placeholder",
            event_type="dossier_version",
            event_index=next_version_number,
            timestamp_ns=timestamp_ns,
            causality_id=command.causality_id,
            event_schema_version="v0",
            event_seq=0,
        )

        terminal_state = case_next_state

        # Use canonical IR output (forbidden states now unrepresentable)
        artifacts = derive_generate_artifacts(
            command=command,
            timestamp_ns=timestamp_ns,
            version_id=version_id,
            version_number=next_version_number,
            engine_data=canonical_ir.to_dict(),
            engine_metadata={
                **dict(engine_out.metadata),
                "execution_path": execution_path,
                "task_id": task_contract.task_id,
                "canonical_ir_version": canonical_ir.version_id,
                "reasoning_version_id": canonical_ir.reasoning_version_id,
            },
            event_schema_version="v0",
            event_seqs=(0, 1),
            domain_adapter=self._domain_adapter,
        )

        # Gate 5 pre-commit enforcement:
        # local observable invariance must hold during execution, not only at replay time.
        # All validation before this point; no partial artifact emission on failure.
        candidate_env = PI_O.project_from_artifacts(
            command=command,
            snapshot_data=artifacts.snapshot.data,
            events=artifacts.events,
            outbox_entries=artifacts.outbox_entries,
            projection_version="pcl-v1",
        )
        # Path-variation check: construction must be invariant to admissible ordering differences.
        reordered_env = PI_O.project_from_artifacts(
            command=command,
            snapshot_data=artifacts.snapshot.data,
            events=tuple(reversed(artifacts.events)),
            outbox_entries=tuple(reversed(artifacts.outbox_entries)),
            projection_version="pcl-v1",
        )
        if not EQUIV_O.equivalent(candidate_env, reordered_env):
            raise SemanticsError(
                code=StableErrorCode.VALIDATION_FAILED,
                message="Observable envelope construction invariance failed",
            )
        try:
            validate_envelope_contract(candidate_env)
            validate_envelope_contract(reordered_env)
            enforce_admissible_step(
                current=candidate_env,
                next_envelope=reordered_env,
                relation=EQUIV_O,
                reason="reordered artifact view",
            )
            metadata_variant = evaluator_metadata_rename_transform(candidate_env)
            enforce_admissible_step(
                current=candidate_env,
                next_envelope=metadata_variant,
                relation=EQUIV_O,
                reason="evaluator metadata semantic-neutral variation",
            )
        except ValueError as exc:
            raise SemanticsError(
                code=StableErrorCode.VALIDATION_FAILED,
                message=f"Observable envelope contract validation failed: {exc}",
            ) from exc

        usage_deltas = _default_usage_deltas(organization_id=command.organization_id)

        # T2: atomic durable commit
        ack = self._tx.commit_generate_t2(
            command=command,
            computed_data=artifacts.snapshot.data,
            version_number=next_version_number,
            version_id=version_id,
            case_next_state=terminal_state.value,
            events=artifacts.events,
            outbox_entries=artifacts.outbox_entries,
            idempotency_fingerprint=execution_id,
            usage_deltas=usage_deltas,
            timestamp_ns=timestamp_ns,
        )

        return ack
