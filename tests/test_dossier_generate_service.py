from __future__ import annotations

from typing import Any

import pytest

from egregore.application.dossier_generate_service import (
    DossierGenerateRequest,
    DossierGenerateService,
)
from egregore.application.in_memory_dossier_adapters import (
    AllowAllAuthzProvider,
    InMemoryCaseStore,
    InMemoryIdempotencyStore,
    InMemoryTransactionalPersistence,
)
from egregore.application.semantics_executor import (
    CorePlaneGenerateDossierExecutor,
    GenerateDossierEngineResult,
    derive_execution_id,
)
from egregore.domain.semantics_models import CaseState, GenerateDossierCommand


def deterministic_engine_policy() -> Any:
    """
    Returns an engine policy function compatible with CorePlaneGenerateDossierExecutor.

    The executor deserializes this engine_out.data via `deserialize_to_canonical_ir`.
    We intentionally return a payload with:
    - no forbidden top-level keys
    - no required semantic layers (fact_layer/interpretation_layer/etc.)
    This yields an IR with an empty statements tuple, which the CBI-0 governance chain
    can still satisfy (it only enforces projection scope when IR statements are present).
    """

    def _policy(cmd: Any) -> GenerateDossierEngineResult:
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

    return _policy


def _mk_service(*, tx: InMemoryTransactionalPersistence) -> DossierGenerateService:
    case_store = tx.case_store
    idempotency = tx.idempotency

    executor = CorePlaneGenerateDossierExecutor(
        authz=AllowAllAuthzProvider(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=deterministic_engine_policy(),
    )
    return DossierGenerateService(executor=executor)


def _mk_request(
    *,
    organization_id: str = "org_1",
    case_id: str = "case_1",
    fingerprint: str = "fp_1",
) -> DossierGenerateRequest:
    return DossierGenerateRequest(
        organization_id=organization_id,
        case_id=case_id,
        actor_id="actor_api_key_1",
        input_fingerprint=fingerprint,
        engine_version="engine_vA",
        policy_version="policy_v1",
        input_payload={"raw": "messy legal notes"},
        causality_id="cmd-1",
        request_id="req-1",
        timestamp_ns=None,  # service will derive deterministically
    )


def test_service_idempotency_suppresses_duplicate_commits() -> None:
    case_store = InMemoryCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.active,
        next_version=1,
    )

    idempotency = InMemoryIdempotencyStore()
    tx = InMemoryTransactionalPersistence(
        idempotency=idempotency, case_store=case_store
    )

    service = _mk_service(tx=tx)

    req = _mk_request(fingerprint="fp-same")

    ack1 = service.generate(request=req)
    assert ack1.http_status == 200
    assert tx.commit_count == 1

    ack2 = service.generate(request=req)
    assert ack2.http_status == 200
    assert tx.commit_count == 1, "idempotent retry must not call T2 twice"

    # Duplicate path should not return outbox IDs (executor returns outbox_ids=None when idempotency hit).
    assert ack2.outbox_ids is None

    # No extra persisted rows.
    assert len(tx.snapshots) == 1
    assert len(tx.events) == 2
    assert len(tx.outbox) == 1
    assert len(tx.usage) == 1


def test_service_fail_closed_on_commit_failure() -> None:
    case_store = InMemoryCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.active,
        next_version=1,
    )

    idempotency = InMemoryIdempotencyStore()
    tx = InMemoryTransactionalPersistence(
        idempotency=idempotency, case_store=case_store, fail_on_commit=True
    )

    service = _mk_service(tx=tx)

    req = _mk_request(fingerprint="fp-failclosed")

    with pytest.raises(RuntimeError, match="Simulated T2 commit failure"):
        service.generate(request=req)

    # Fail-closed: no successful idempotency mapping and no persisted artifacts.
    assert tx.commit_count == 0

    cmd = GenerateDossierCommand(
        organization_id=req.organization_id,
        case_id=req.case_id,
        actor_id=req.actor_id,
        input_fingerprint=req.input_fingerprint,
        engine_version=req.engine_version,
        policy_version=req.policy_version,
        input_payload=req.input_payload,
        causality_id=req.causality_id,
        request_id=req.request_id,
    )
    execution_id = derive_execution_id(command=cmd)

    assert idempotency.get_success_result(input_fingerprint=execution_id) is None
    assert tx.snapshots == []
    assert tx.events == []
    assert tx.outbox == []
    assert tx.usage == []
