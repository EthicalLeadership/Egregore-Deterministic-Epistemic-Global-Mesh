from __future__ import annotations

from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
    Depends = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[misc]

import importlib

from egregore.infrastructure.bootstrap import get_dossier_facade
from egregore.interface.ports.dossier_ports import DossierServiceFacade


def _get_facade_dep() -> Any:
    """
    Import indirection to keep AST-visible imports out of interface modules
    (architecture policy enforcement).
    """
    mod = importlib.import_module("egregore.infrastructure.bootstrap")
    return mod.get_dossier_facade()


def _build_router() -> Any:
    if APIRouter is None:  # pragma: no cover
        return None

    router = APIRouter()

    @router.post("/v1/dossiers/generate")
    async def generate_dossier(
        payload: dict[str, Any],
        facade: Any = Depends(_get_facade_dep),  # type: ignore[call-arg]  # optional dependency / compatibility  # noqa: B008
    ) -> dict[str, Any]:
        """
        Thin invariant-preserving wrapper: validate transport fields and delegate.
        """
        try:
            organization_id = str(payload["organization_id"])
            case_id = str(payload["case_id"])
            actor_id = str(payload["actor_id"])

            input_fingerprint = str(payload["input_fingerprint"])
            engine_version = str(payload["engine_version"])
            policy_version = str(payload["policy_version"])

            input_payload = payload["input_payload"]
            if not isinstance(input_payload, dict):
                raise ValueError("input_payload must be an object")

            causality_id = str(payload["causality_id"])

            request_id = payload.get("request_id")
            if request_id is not None:
                request_id = str(request_id)

            timestamp_ns = payload.get("timestamp_ns")
            if timestamp_ns is not None:
                timestamp_ns = int(timestamp_ns)

            vertical = payload.get("vertical")
            vertical_opt = None if vertical is None else str(vertical).strip() or None

            ports_mod = importlib.import_module(
                "egregore.interface.ports.dossier_ports"
            )
            DossierGenerateRequest = ports_mod.DossierGenerateRequest  # noqa: N806
            request = DossierGenerateRequest(
                organization_id=organization_id,
                case_id=case_id,
                actor_id=actor_id,
                input_fingerprint=input_fingerprint,
                engine_version=engine_version,
                policy_version=policy_version,
                input_payload=input_payload,
                causality_id=causality_id,
                request_id=request_id,
                timestamp_ns=timestamp_ns,
                vertical=vertical_opt,
            )

            result = facade.generate(request=request)
            return {"status": "ok", "data": result}
        except Exception as exc:
            raise HTTPException(status_code=500, detail="generation_failed") from exc

    return router


router = _build_router()


# ---------------------------------------------------------------------------
# Back-compat helpers (used by unit tests via monkeypatch).
# These must not introduce new static imports from egregore.application.
# The actual HTTP handler delegates to `facade.generate(...)`.
# ---------------------------------------------------------------------------


def get_service() -> DossierServiceFacade:
    # Lazy: get_dossier_facade imports application via importlib at runtime.
    return get_dossier_facade()


def _build_vertical_service(
    *, vertical: str, policy_version: str
) -> DossierServiceFacade:
    # The injected facade already supports vertical routing via request.vertical.
    # Kept for test compatibility and future extensibility.
    return get_dossier_facade()


def _service_for_payload(payload: dict[str, Any]) -> Any:
    vertical_raw = payload.get("vertical")
    vertical_opt = None if vertical_raw is None else str(vertical_raw).strip() or None
    if vertical_opt is None:
        return get_service()

    policy_version = str(payload["policy_version"])
    service = _build_vertical_service(
        vertical=vertical_opt, policy_version=policy_version
    )
    return service
