from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pytest

from egregore.application.semantics_executor import (
    CorePlaneGenerateDossierExecutor,
    derive_execution_id,
)
from egregore.domain.legal_agent.legal_models import (
    InferenceNode,
    LegalAgentVersion,
    LegalAnalysisOutput,
    RuleMatch,
)
from egregore.domain.semantics_models import (
    AuditEvent,
    CaseState,
    CommandAck,
    CommandResult,
    GenerateDossierCommand,
    OutboxEntry,
    SemanticsError,
    StableErrorCode,
)
from egregore.interface.semantics_ports import (
    IAuthzProvider,
    ICaseStore,
    IIdempotencyStore,
    ISemanticsDomainAdapter,
    ITransactionalPersistence,
)


class FakeAuthz(IAuthzProvider):
    def authorize_generate(self, *, command: GenerateDossierCommand) -> None:
        # Always allow for unit tests.
        return None


class FakeCaseStore(ICaseStore):
    def __init__(self) -> None:
        self._state_by_key: dict[tuple[str, str], str] = {}
        self._next_version_by_key: dict[tuple[str, str], int] = {}

    def seed(
        self, *, organization_id: str, case_id: str, state: CaseState, next_version: int
    ) -> None:
        self._state_by_key[(organization_id, case_id)] = state.value
        self._next_version_by_key[(organization_id, case_id)] = next_version

    def get_case_state(self, *, organization_id: str, case_id: str) -> str:
        return self._state_by_key[(organization_id, case_id)]

    def get_next_version_number(self, *, organization_id: str, case_id: str) -> int:
        return self._next_version_by_key[(organization_id, case_id)]


class FakeIdempotency(IIdempotencyStore):
    def __init__(self) -> None:
        self._success_by_fingerprint: dict[str, CommandResult] = {}

    def get_success_result(self, *, input_fingerprint: str) -> CommandResult | None:
        return self._success_by_fingerprint.get(input_fingerprint)

    def put_success_result(
        self, *, input_fingerprint: str, result: CommandResult
    ) -> None:
        self._success_by_fingerprint[input_fingerprint] = result


class FakeTx(ITransactionalPersistence):
    def __init__(
        self,
        *,
        idempotency: FakeIdempotency,
        fail_on_commit: bool = False,
        case_store: FakeCaseStore,
    ) -> None:
        self.idempotency = idempotency
        self.fail_on_commit = fail_on_commit
        self.case_store = case_store

        self.snapshots: list[tuple[str, str, int, str, Mapping[str, Any]]] = []
        self.events: list[AuditEvent] = []
        self.outbox: list[OutboxEntry] = []
        self.usage: list[tuple[str, str, int]] = []
        self.commit_count = 0

    def commit_generate_t2(
        self,
        *,
        command: GenerateDossierCommand,
        computed_data: Mapping,
        version_number: int,
        version_id: str,
        case_next_state: str,
        events: Iterable[AuditEvent],
        outbox_entries: Iterable[OutboxEntry],
        idempotency_fingerprint: str,
        usage_deltas: Iterable[tuple[str, str, int]],
        timestamp_ns: int,
    ) -> CommandAck:
        if self.fail_on_commit:
            raise RuntimeError("Simulated T2 commit failure")

        self.commit_count += 1

        events_list = list(events)
        outbox_list = list(outbox_entries)
        usage_list = list(usage_deltas)

        # Persist snapshot + events + outbox + usage in a single “atomic” method.
        self.snapshots.append(
            (
                command.organization_id,
                command.case_id,
                version_number,
                version_id,
                dict(computed_data),
            )
        )
        self.events.extend(events_list)
        self.outbox.extend(outbox_list)
        self.usage.extend(usage_list)

        # Update idempotency mapping.
        result = CommandResult(
            organization_id=command.organization_id,
            case_id=command.case_id,
            version_id=version_id,
            version_number=version_number,
            engine_version=command.engine_version,
            policy_version=command.policy_version,
            data=dict(computed_data),
        )
        self.idempotency.put_success_result(
            input_fingerprint=idempotency_fingerprint, result=result
        )

        # Advance case next version (simple monotonic progression).
        self.case_store.seed(
            organization_id=command.organization_id,
            case_id=command.case_id,
            state=CaseState(case_next_state),
            next_version=version_number + 1,
        )

        return CommandAck(
            http_status=200,
            result=result,
            outbox_ids=[o.outbox_id for o in outbox_list],
        )


def deterministic_engine_policy(command: GenerateDossierCommand) -> Any:
    # Deterministic compute: embed engine/policy versions in output.
    return {
        "data": {
            "case_overview": {
                "engine": command.engine_version,
                "policy": command.policy_version,
            },
            "canonical_sections": ["case_overview", "parties", "facts", "timeline"],
        },
        "metadata": {"input_fingerprint": command.input_fingerprint},
    }


def compute_adapter(cmd: GenerateDossierCommand):
    # Match executor’s expected GenerateDossierEngineResult shape.
    out = deterministic_engine_policy(cmd)
    from egregore.application.semantics_executor import GenerateDossierEngineResult

    return GenerateDossierEngineResult(data=out["data"], metadata=out["metadata"])


def _terminal_legal_output() -> LegalAnalysisOutput:
    version = LegalAgentVersion(
        rule_registry_version="v1.0", inference_engine_version="v1.0"
    )
    rule = RuleMatch(
        rule_id="rule_workplace_comms",
        rule_text="Workplace comms may establish conduct patterns.",
        jurisdiction="general",
        matched_fact_ids=("s1",),
        confidence=0.75,
    )
    node = InferenceNode(
        node_id="node_0",
        premise_rule_ids=(rule.rule_id,),
        premise_fact_ids=("s1",),
        conclusion="Rule may apply",
        confidence=0.75,
        uncertainty_reason="",
    )
    return LegalAnalysisOutput(
        case_id="case_1",
        issues_identified=("Potential issue",),
        applicable_rules=(rule,),
        supporting_evidence_ids=("s1",),
        inference_chain=(node,),
        confidence_scores={"node_0": 0.75},
        uncertainty_flags=(),
        reasoning_version="v1.0:v1.0",
        agent_version=version,
        prohibited_conclusions=(),
    )


def compute_adapter_terminal_output(cmd: GenerateDossierCommand):
    from egregore.application.semantics_executor import GenerateDossierEngineResult

    return GenerateDossierEngineResult(
        data=_terminal_legal_output(),
        metadata={"input_fingerprint": cmd.input_fingerprint},
    )


def make_command(*, fingerprint: str = "fp-1") -> GenerateDossierCommand:
    return GenerateDossierCommand(
        organization_id="org_1",
        case_id="case_1",
        actor_id="actor_api_key_1",
        input_fingerprint=fingerprint,
        engine_version="engine_vA",
        policy_version="policy_v1",
        input_payload={"raw": "messy legal notes"},
        causality_id="cmd-1",
        request_id="req-1",
    )


def test_idempotency_duplicate_suppresses_snapshot_events_outbox_and_usage() -> None:
    case_store = FakeCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.created,
        next_version=1,
    )

    idempotency = FakeIdempotency()
    tx = FakeTx(idempotency=idempotency, case_store=case_store)

    executor = CorePlaneGenerateDossierExecutor(
        authz=FakeAuthz(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_adapter,
    )

    cmd = make_command(fingerprint="fp-same")

    ack1 = executor.handle_generate_dossier(command=cmd, timestamp_ns=123)
    assert ack1.http_status == 200
    assert tx.commit_count == 1
    assert len(tx.snapshots) == 1
    assert len(tx.events) == 2
    assert len(tx.outbox) == 1
    assert len(tx.usage) == 1

    ack2 = executor.handle_generate_dossier(
        command=cmd, timestamp_ns=999
    )  # timestamp differs; fingerprint same
    assert ack2.http_status == 200
    assert tx.commit_count == 1, "duplicate must not call T2 commit again"

    # Snapshots/events/outbox/usage unchanged.
    assert len(tx.snapshots) == 1
    assert len(tx.events) == 2
    assert len(tx.outbox) == 1
    assert len(tx.usage) == 1

    # Result returned is the original successful result.
    assert ack2.result == ack1.result
    assert ack2.outbox_ids is None


def test_forbidden_state_transition_rejected_no_mutation() -> None:
    case_store = FakeCaseStore()
    # If case is already generating, executor forbids overlapping generate.
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.generating,
        next_version=1,
    )

    idempotency = FakeIdempotency()
    tx = FakeTx(idempotency=idempotency, case_store=case_store)

    executor = CorePlaneGenerateDossierExecutor(
        authz=FakeAuthz(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_adapter,
    )

    cmd = make_command(fingerprint="fp-forbidden")
    with pytest.raises(SemanticsError) as excinfo:
        executor.handle_generate_dossier(command=cmd, timestamp_ns=123)

    assert excinfo.value.code == StableErrorCode.FORBIDDEN_STATE_TRANSITION
    assert tx.commit_count == 0
    execution_id = derive_execution_id(command=cmd)
    assert idempotency.get_success_result(input_fingerprint=execution_id) is None
    assert tx.snapshots == []
    assert tx.events == []
    assert tx.outbox == []
    assert tx.usage == []


def test_fail_closed_on_t2_commit_failure_no_success_mapping_no_side_effects() -> None:
    case_store = FakeCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.created,
        next_version=1,
    )

    idempotency = FakeIdempotency()
    tx = FakeTx(idempotency=idempotency, case_store=case_store, fail_on_commit=True)

    executor = CorePlaneGenerateDossierExecutor(
        authz=FakeAuthz(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_adapter,
    )

    cmd = make_command(fingerprint="fp-failclosed")
    with pytest.raises(RuntimeError):
        executor.handle_generate_dossier(command=cmd, timestamp_ns=123)

    # Fail-closed: no persisted snapshot/events/outbox/usage, no idempotency success mapping.
    assert tx.commit_count == 0
    execution_id = derive_execution_id(command=cmd)
    assert idempotency.get_success_result(input_fingerprint=execution_id) is None
    assert tx.snapshots == []
    assert tx.events == []
    assert tx.outbox == []
    assert tx.usage == []


def test_events_include_causality_id_and_are_rebuildable() -> None:
    case_store = FakeCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.created,
        next_version=1,
    )

    idempotency = FakeIdempotency()
    tx = FakeTx(idempotency=idempotency, case_store=case_store)

    executor = CorePlaneGenerateDossierExecutor(
        authz=FakeAuthz(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_adapter,
    )

    cmd = make_command(fingerprint="fp-causality")
    ack = executor.handle_generate_dossier(command=cmd, timestamp_ns=555)

    assert ack.http_status == 200
    assert len(tx.events) == 2
    assert all(e.causality_id == cmd.causality_id for e in tx.events)

    # Semantic closure v0 checks: schema + logical ordering.
    assert [e.event_seq for e in tx.events] == [0, 1]
    assert all(e.event_schema_version == "v0" for e in tx.events)

    # “Projection rebuild” contract (Phase 1 test):
    # In this MVP, canonical projection can be derived from the persisted snapshot data.
    assert len(tx.snapshots) == 1
    _, _, _, _, snapshot_data = tx.snapshots[0]
    # Canonical IR snapshot now contains statements in typed semantic format
    assert "statements" in snapshot_data
    assert "reasoning_guard_invariant" in snapshot_data  # BIOK invariant marker


def test_requested_event_contains_task_contract_and_execution_path() -> None:
    case_store = FakeCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.created,
        next_version=1,
    )

    idempotency = FakeIdempotency()
    tx = FakeTx(idempotency=idempotency, case_store=case_store)

    executor = CorePlaneGenerateDossierExecutor(
        authz=FakeAuthz(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_adapter,
    )

    cmd = make_command(fingerprint="fp-task-contract")
    executor.handle_generate_dossier(command=cmd, timestamp_ns=111)

    requested_event = tx.events[0]
    generated_event = tx.events[1]

    task_contract = requested_event.payload["task_contract"]
    assert task_contract["task_id"] == cmd.causality_id
    assert task_contract["intent"] == "generate_dossier"
    assert task_contract["policy_level"] == "strict"
    assert task_contract["replayable"] is True
    assert task_contract["expected_outputs"] == [
        "snapshot",
        "audit_events",
        "outbox_entries",
    ]

    assert generated_event.payload["metadata"]["task_id"] == cmd.causality_id
    assert generated_event.payload["metadata"]["execution_path"] == [
        "INIT",
        "VALIDATE",
        "PLAN",
        "EXECUTE",
        "VERIFY",
        "COMMIT",
    ]


class FakeDomainAdapter(ISemanticsDomainAdapter):
    def requested_event_type(self) -> str:
        return "TASK_REQUESTED"

    def generated_event_type(self) -> str:
        return "TASK_COMPUTED"

    def outbox_side_effect_type(self) -> str:
        return "ADAPTER_DISPATCH"

    def outbox_payload(
        self, *, engine_data: Mapping[str, Any], generated_event_type: str
    ) -> Mapping[str, Any]:
        return {
            "adapter": "fake",
            "event": generated_event_type,
            "payload": dict(engine_data),
        }


def test_executor_supports_domain_adapter_for_event_and_outbox_mapping() -> None:
    case_store = FakeCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.active,
        next_version=1,
    )

    idempotency = FakeIdempotency()
    tx = FakeTx(idempotency=idempotency, case_store=case_store)

    executor = CorePlaneGenerateDossierExecutor(
        authz=FakeAuthz(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_adapter,
        domain_adapter=FakeDomainAdapter(),
    )

    cmd = make_command(fingerprint="fp-domain-adapter")
    executor.handle_generate_dossier(command=cmd, timestamp_ns=333)

    assert tx.events[0].event_type == "TASK_REQUESTED"
    assert tx.events[1].event_type == "TASK_COMPUTED"
    assert tx.outbox[0].side_effect_type == "ADAPTER_DISPATCH"
    assert tx.outbox[0].payload["adapter"] == "fake"


def test_executor_fails_when_cbi_projection_descriptor_missing() -> None:
    case_store = FakeCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.active,
        next_version=1,
    )

    idempotency = FakeIdempotency()
    tx = FakeTx(idempotency=idempotency, case_store=case_store)

    executor = CorePlaneGenerateDossierExecutor(
        authz=FakeAuthz(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_adapter,
        projection_descriptors={},
        overlap_classifications=[],
    )

    with pytest.raises(SemanticsError) as excinfo:
        executor.handle_generate_dossier(
            command=make_command(fingerprint="fp-cbi-missing"), timestamp_ns=333
        )

    assert excinfo.value.code == StableErrorCode.VALIDATION_FAILED
    assert "CBI-0 governance validation failed" in excinfo.value.message
    assert tx.commit_count == 0


def test_executor_fails_when_terminal_output_attempts_implicit_ir_synthesis() -> None:
    case_store = FakeCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.active,
        next_version=1,
    )

    idempotency = FakeIdempotency()
    tx = FakeTx(idempotency=idempotency, case_store=case_store)

    executor = CorePlaneGenerateDossierExecutor(
        authz=FakeAuthz(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_adapter_terminal_output,
    )

    with pytest.raises(SemanticsError) as excinfo:
        executor.handle_generate_dossier(
            command=make_command(fingerprint="fp-m3-routing"), timestamp_ns=444
        )

    assert excinfo.value.code == StableErrorCode.VALIDATION_FAILED
    assert "CBI-0 composition guard failed" in excinfo.value.message
    assert tx.commit_count == 0


def test_case_commit_uses_validated_next_state() -> None:
    """
    Regression test for the hardcoded CaseState.active commit bug.

    For current_state=active, the deterministic state machine computes case_next_state=generating.
    The commit must persist 'generating', not 'active'.
    """
    case_store = FakeCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.active,
        next_version=1,
    )

    idempotency = FakeIdempotency()
    tx = FakeTx(idempotency=idempotency, case_store=case_store)

    executor = CorePlaneGenerateDossierExecutor(
        authz=FakeAuthz(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_adapter,
    )

    cmd = make_command(fingerprint="fp-state-next")
    executor.handle_generate_dossier(command=cmd, timestamp_ns=123)

    persisted_state_raw = case_store.get_case_state(
        organization_id=cmd.organization_id, case_id=cmd.case_id
    )
    assert persisted_state_raw == CaseState.generating.value


def test_idempotency_invalidated_by_engine_version() -> None:
    """
    Idempotency key must be CEIM execution_id and therefore version-aware.

    Same semantic input (input_fingerprint + org/case + causality_id),
    but different engine_version => different execution_id => no T1 cache hit => T2 commit runs again.
    """
    case_store = FakeCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.created,
        next_version=1,
    )

    idempotency = FakeIdempotency()
    tx = FakeTx(idempotency=idempotency, case_store=case_store)

    executor = CorePlaneGenerateDossierExecutor(
        authz=FakeAuthz(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_adapter,
    )

    cmd1 = make_command(fingerprint="fp-idempotency-version")
    cmd2 = GenerateDossierCommand(
        organization_id=cmd1.organization_id,
        case_id=cmd1.case_id,
        actor_id=cmd1.actor_id,
        input_fingerprint=cmd1.input_fingerprint,
        engine_version="engine_vB",
        policy_version=cmd1.policy_version,
        input_payload=cmd1.input_payload,
        causality_id=cmd1.causality_id,
        request_id=cmd1.request_id,
    )

    executor.handle_generate_dossier(command=cmd1, timestamp_ns=111)
    assert tx.commit_count == 1

    executor.handle_generate_dossier(command=cmd2, timestamp_ns=222)
    assert tx.commit_count == 2

    execution_id_1 = derive_execution_id(command=cmd1)
    execution_id_2 = derive_execution_id(command=cmd2)
    assert idempotency.get_success_result(input_fingerprint=execution_id_1) is not None
    assert idempotency.get_success_result(input_fingerprint=execution_id_2) is not None
