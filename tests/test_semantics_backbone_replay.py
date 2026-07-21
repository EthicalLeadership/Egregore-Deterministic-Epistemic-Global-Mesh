from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from egregore.application.semantics_executor import (
    CorePlaneGenerateDossierExecutor,
    GenerateDossierEngineResult,
)
from egregore.application.semantics_replay_interpreter import (
    CorePlaneReplayInterpreter,
)
from egregore.domain.semantics_models import (
    AuditEvent,
    CaseState,
    CommandAck,
    CommandResult,
    GenerateDossierCommand,
    OutboxEntry,
)
from egregore.interface.semantics_ports import (
    IAuthzProvider,
    ICaseStore,
    IIdempotencyStore,
    ITransactionalPersistence,
)


class FakeAuthz(IAuthzProvider):
    def authorize_generate(self, *, command: GenerateDossierCommand) -> None:
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
        self, *, idempotency: FakeIdempotency, case_store: FakeCaseStore
    ) -> None:
        self.idempotency = idempotency
        self.case_store = case_store
        self.snapshots: list[tuple[str, str, int, str, Mapping[str, Any]]] = []
        self.events: list[AuditEvent] = []
        self.outbox: list[OutboxEntry] = []
        self.commit_count = 0
        self.usage: list[tuple[str, str, int]] = []

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
        self.commit_count += 1

        self.snapshots.append(
            (
                command.organization_id,
                command.case_id,
                version_number,
                version_id,
                dict(computed_data),
            )
        )
        events_list = list(events)
        outbox_list = list(outbox_entries)
        usage_list = list(usage_deltas)

        self.events.extend(events_list)
        self.outbox.extend(outbox_list)
        self.usage.extend(usage_list)

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


def compute_adapter(cmd: GenerateDossierCommand) -> GenerateDossierEngineResult:
    return GenerateDossierEngineResult(
        data={
            "case_overview": {
                "engine": cmd.engine_version,
                "policy": cmd.policy_version,
            },
            "canonical_sections": ["case_overview", "parties", "facts", "timeline"],
        },
        metadata={"input_fingerprint": cmd.input_fingerprint},
    )


def make_command(
    *, fingerprint: str = "fp-replay", causality_id: str = "cmd-1"
) -> GenerateDossierCommand:
    return GenerateDossierCommand(
        organization_id="org_1",
        case_id="case_1",
        actor_id="actor_api_key_1",
        input_fingerprint=fingerprint,
        engine_version="engine_vA",
        policy_version="policy_v1",
        input_payload={"raw": "messy legal notes"},
        causality_id=causality_id,
        request_id="req-1",
    )


def test_replay_interpreter_equivalence_matches_committed_artifacts() -> None:
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

    replay = CorePlaneReplayInterpreter(compute_engine_policy=compute_adapter)

    cmd = make_command(fingerprint="fp-replay", causality_id="cmd-causality-123")
    timestamp_ns = 777

    ack = executor.handle_generate_dossier(command=cmd, timestamp_ns=timestamp_ns)
    assert ack.http_status == 200
    assert tx.commit_count == 1
    assert len(tx.snapshots) == 1
    assert len(tx.events) == 2
    assert len(tx.outbox) == 1

    _, _, _, version_id, snapshot_data = tx.snapshots[0]
    result = replay.replay_equivalence(
        command=cmd,
        timestamp_ns=timestamp_ns,
        version_id=version_id,
        snapshot_data=snapshot_data,
        events=tx.events,
        outbox_entries=tx.outbox,
    )
    assert result.ok is True, "; ".join(result.failures)
    assert tuple(result.governance_trace) == ("M2", "M1", "M4", "SWEEP")


def test_replay_interpreter_fails_when_cbi_registry_descriptor_missing() -> None:
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

    replay = CorePlaneReplayInterpreter(
        compute_engine_policy=compute_adapter,
        projection_descriptors={},
        overlap_classifications=[],
    )

    cmd = make_command(
        fingerprint="fp-replay-missing-descriptor", causality_id="cmd-causality-456"
    )
    timestamp_ns = 888
    ack = executor.handle_generate_dossier(command=cmd, timestamp_ns=timestamp_ns)
    assert ack.http_status == 200

    _, _, _, version_id, snapshot_data = tx.snapshots[0]
    result = replay.replay_equivalence(
        command=cmd,
        timestamp_ns=timestamp_ns,
        version_id=version_id,
        snapshot_data=snapshot_data,
        events=tx.events,
        outbox_entries=tx.outbox,
    )

    assert result.ok is False
    assert any(
        "CBI-0 replay governance validation failed" in f for f in result.failures
    )
