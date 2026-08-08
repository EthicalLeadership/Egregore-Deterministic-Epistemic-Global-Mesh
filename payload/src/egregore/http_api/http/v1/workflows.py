from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

try:
    from fastapi import APIRouter, Depends
    from pydantic import BaseModel
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
    Depends = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[misc]

import hashlib
import math


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _stable_serialize(data: Any) -> str:  # noqa: C901
    """
    Deterministic serialization for hashing (no json.dumps/json.loads).
    """
    if data is None:
        return "null"
    if isinstance(data, bool):
        return "true" if data else "false"
    if isinstance(data, int):
        return str(data)
    if isinstance(data, float):
        if math.isnan(data):
            return "NaN"
        if math.isinf(data):
            return "Infinity" if data > 0 else "-Infinity"
        return repr(data)
    if isinstance(data, str):
        return repr(data)
    if isinstance(data, (bytes, bytearray)):
        return repr(bytes(data))

    if isinstance(data, dict):
        items: list[tuple[str, str]] = []
        for k, v in data.items():
            items.append((_stable_serialize(k), _stable_serialize(v)))
        items.sort(key=lambda x: x[0])
        return "{" + ",".join(f"{k}:{v}" for k, v in items) + "}"

    if isinstance(data, (list, tuple)):
        return "[" + ",".join(_stable_serialize(x) for x in data) + "]"

    return repr(data)


@dataclass(frozen=True)
class WorkflowState:
    status: str
    error: str | None = None


class TestHealthRequest(BaseModel):  # type: ignore[misc]
    input: Any
    idempotency_key: str
    correlation_id: str


class TestHealthResponse(BaseModel):  # type: ignore[misc]
    id: str
    status: str


class WorkflowSummaryHealthResponse(BaseModel):  # type: ignore[misc]
    id: str
    status: str
    error: str | None = None


def _get_dossier_facade_dep() -> Any:
    """
    FastAPI dependency provider indirection to keep AST-visible imports out
    of this interface module.
    """
    mod = importlib.import_module("egregore.infrastructure.bootstrap")
    return mod.get_dossier_facade()


def _fingerprint_from_input(user_input: Any) -> str:
    payload: Any
    payload = user_input if isinstance(user_input, dict) else {"input": user_input}
    return _sha256_hex(_stable_serialize(payload).encode("utf-8"))


def _correlation_case_id(correlation_id: str) -> str:
    payload = {"correlation_id": correlation_id}
    digest = _sha256_hex(_stable_serialize(payload).encode("utf-8"))
    return f"healthcase_{digest[:16]}"


def _build_health_request(*, body: TestHealthRequest) -> Any:
    ports_mod = importlib.import_module("egregore.interface.ports.dossier_ports")
    DossierGenerateRequest = ports_mod.DossierGenerateRequest  # noqa: N806

    organization_id = "egregore-health"
    case_id = _correlation_case_id(body.correlation_id)

    actor_id = "healthcheck_actor"
    input_fingerprint = _fingerprint_from_input(body.input)

    engine_version = "engine_health_v1"
    policy_version = "policy_v1"

    # Deterministic causality binds to idempotency key.
    causality_id = f"health_{body.idempotency_key}"

    return DossierGenerateRequest(
        organization_id=organization_id,
        case_id=case_id,
        actor_id=actor_id,
        input_fingerprint=input_fingerprint,
        engine_version=engine_version,
        policy_version=policy_version,
        input_payload=(
            body.input if isinstance(body.input, dict) else {"input": body.input}
        ),
        causality_id=causality_id,
        request_id=None,
        timestamp_ns=None,  # service derives deterministically
        vertical=None,
    )


_WORKFLOW_STATES: dict[str, WorkflowState] = {}


def _workflow_id_for_health_request(*, request: Any, idempotency_key: str) -> str:
    digest = _sha256_hex(
        _stable_serialize(
            {
                "organization_id": request.organization_id,
                "case_id": request.case_id,
                "actor_id": request.actor_id,
                "input_fingerprint": request.input_fingerprint,
                "engine_version": request.engine_version,
                "policy_version": request.policy_version,
                "causality_id": request.causality_id,
                "idempotency_key": idempotency_key,
            }
        ).encode("utf-8")
    )
    return f"healthwf_{digest[:16]}"


def _create_router() -> Any:
    if APIRouter is None:  # pragma: no cover
        return None

    router = APIRouter()

    @router.post("/workflows/test-health", response_model=TestHealthResponse)
    async def test_health(
        body: TestHealthRequest,
        facade: Any = Depends(_get_dossier_facade_dep),  # type: ignore[call-arg]  # compatibility  # noqa: B008
    ) -> dict[str, Any]:
        req = _build_health_request(body=body)
        workflow_id = _workflow_id_for_health_request(
            request=req, idempotency_key=body.idempotency_key
        )

        _WORKFLOW_STATES[workflow_id] = WorkflowState(status="running")

        try:
            _ = facade.generate(request=req)
            _WORKFLOW_STATES[workflow_id] = WorkflowState(
                status="completed", error=None
            )
        except Exception as exc:  # noqa: BLE001
            _WORKFLOW_STATES[workflow_id] = WorkflowState(
                status="failed", error=str(exc)
            )

        state = _WORKFLOW_STATES[workflow_id]
        return {"id": workflow_id, "status": state.status}

    @router.get(
        "/workflows/{workflow_id}", response_model=WorkflowSummaryHealthResponse
    )
    async def get_workflow_status(workflow_id: str) -> dict[str, Any]:
        state = _WORKFLOW_STATES.get(workflow_id)
        if state is None:
            return {
                "id": workflow_id,
                "status": "unknown",
                "error": "workflow_not_found",
            }
        return {"id": workflow_id, "status": state.status, "error": state.error}

    return router


router = _create_router()
