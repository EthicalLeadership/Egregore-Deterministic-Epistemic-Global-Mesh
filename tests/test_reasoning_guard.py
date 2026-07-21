from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pytest

from egregore.application.semantics_executor import (
    CorePlaneGenerateDossierExecutor,
    GenerateDossierEngineResult,
)
from egregore.domain.semantics.reasoning_guard import (
    enforce_evidence_to_conclusion_boundary,
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
        self.snapshots: list[Mapping[str, Any]] = []

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
        self.snapshots.append(dict(computed_data))
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
        return CommandAck(http_status=200, result=result, outbox_ids=[])


def make_command() -> GenerateDossierCommand:
    return GenerateDossierCommand(
        organization_id="org_1",
        case_id="case_1",
        actor_id="actor_api_key_1",
        input_fingerprint="fp-rg",
        engine_version="engine_vA",
        policy_version="policy_v1",
        input_payload={"raw": "messy legal notes"},
        causality_id="cmd-rg-1",
        request_id="req-1",
    )


def test_reasoning_guard_downgrades_forbidden_interpretation_claims() -> None:
    payload = {
        "fact_layer": {"message_id": "m1"},
        "classification_layer": {"routing": "allowed"},
        "interpretation_layer": {"statements": ["This establishes liability"]},
    }
    out = enforce_evidence_to_conclusion_boundary(payload)
    assert "may indicate" in out["interpretation_layer"]["statements"][0].lower()
    assert "reasoning_guard_invariant" in out


def test_reasoning_guard_downgrades_paraphrased_liability_phrasing() -> None:
    payload = {
        "fact_layer": {"message_id": "m1"},
        "classification_layer": {"routing": "allowed"},
        "interpretation_layer": {
            "statements": ["The record shows liability is proven."]
        },
    }
    out = enforce_evidence_to_conclusion_boundary(payload)
    assert "may indicate" in out["interpretation_layer"]["statements"][0].lower()


def test_reasoning_guard_reject_only_paraphrased_liability_phrasing() -> None:
    payload = {
        "fact_layer": {"message_id": "m1"},
        "classification_layer": {"routing": "allowed"},
        "interpretation_layer": {"statements": ["liability has been established"]},
        "excluded_layer": {},
    }
    with pytest.raises(ValueError) as excinfo:
        enforce_evidence_to_conclusion_boundary(payload)
    assert "Forbidden evidence-to-conclusion phrasing" in str(excinfo.value)


def test_reasoning_guard_rejects_legal_conclusion_output() -> None:
    payload = {
        "fact_layer": {"message_id": "m1"},
        "classification_layer": {"routing": "allowed"},
        "legal_conclusions": ["Liability established"],
    }
    with pytest.raises(ValueError):
        enforce_evidence_to_conclusion_boundary(payload)


def test_executor_fails_closed_on_forbidden_legal_output() -> None:
    case_store = FakeCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.active,
        next_version=1,
    )
    idempotency = FakeIdempotency()
    tx = FakeTx(idempotency=idempotency, case_store=case_store)

    def compute_adapter(_: GenerateDossierCommand) -> GenerateDossierEngineResult:
        # Canonical IR deserialization rejects forbidden fields at representation boundary
        return GenerateDossierEngineResult(
            data={
                "fact_layer": {"message_id": "m1"},
                "classification_layer": {"routing": "allowed"},
                "legal_conclusions": [
                    "This proves wrongdoing"
                ],  # forbidden at deserialization
            },
            metadata={},
        )

    executor = CorePlaneGenerateDossierExecutor(
        authz=FakeAuthz(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_adapter,
    )

    with pytest.raises(SemanticsError) as excinfo:
        executor.handle_generate_dossier(command=make_command(), timestamp_ns=123)
    assert excinfo.value.code == StableErrorCode.VALIDATION_FAILED
    assert "Canonical IR deserialization failed" in excinfo.value.message
