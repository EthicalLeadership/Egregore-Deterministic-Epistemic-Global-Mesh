"""Reproducible Fusion Engine (RFE) FastAPI router."""

from __future__ import annotations

from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Query
    from pydantic import BaseModel
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment,misc]
    HTTPException = Exception  # type: ignore[assignment,misc]
    Query = None  # type: ignore[assignment,misc]
    BaseModel = object  # type: ignore[assignment,misc]

from egregore.rfe.config import RFEConfig, load_rfe_config
from egregore.rfe.engine import (
    feedback_to_stream,
    manifest_fingerprint,
    reproducible_fusion,
)
from egregore.rfe.models import (
    ConfigResponse,
    FeedbackRequest,
    FeedbackResponse,
    GenerateResponse,
    HealthResponse,
    VersionInfo,
    VersionsResponse,
)
from egregore.rfe.provenance_store import (
    append_report_event,
    get_manifest_by_hash,
    get_provenance_store,
)
from egregore.rfe.security import FutureTimestampError
from egregore.rfe.sensitivity import generate_sensitivity_report


def _load_config() -> RFEConfig:
    return RFEConfig(load_rfe_config())


def _build_router() -> Any:
    if APIRouter is None:  # pragma: no cover
        return None

    router = APIRouter()

    @router.post("/generate", response_model=GenerateResponse)
    async def generate(body: dict[str, Any]) -> dict[str, Any]:
        """Submit a manifest and receive a reproducible report + decision log + hashes."""
        config = _load_config()
        try:
            result = reproducible_fusion(manifest=body, config=config.raw)
        except FutureTimestampError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid_manifest: {exc}"
            ) from exc

        # Persist as a .zarc event for version history.
        try:
            prov = get_provenance_store(config)
            append_report_event(
                prov,
                case_id=result["report"]["case_id"],
                version_id=result["version_id"],
                report_hash=result["report_hash"],
                decision_log_hash=result["decision_log_hash"],
                report=result["report"],
                manifest_fingerprint=manifest_fingerprint(body),
                manifest=body,
            )
        except Exception as exc:  # noqa: BLE001
            # Provenance append failure must not fail the HTTP response,
            # but it is recorded as a warning in the response metadata.
            result.setdefault("_warnings", []).append(
                f"provenance_append_failed: {exc}"
            )

        return {
            "status": "ok",
            "report": result["report"],
            "report_hash": result["report_hash"],
            "decision_log_hash": result["decision_log_hash"],
            "version_id": result["version_id"],
        }

    @router.post("/feedback", response_model=FeedbackResponse)
    async def feedback(body: dict[str, Any]) -> dict[str, Any]:
        """Submit feedback that becomes a human_feedback stream for the next manifest."""
        try:
            req = FeedbackRequest.model_validate(body)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid_feedback: {exc}"
            ) from exc

        stream = feedback_to_stream(req)
        return {
            "status": "ok",
            "stream": stream,
            "message": (
                "Feedback accepted. Include this stream in the next manifest's "
                "'streams' array to fuse it into the report."
            ),
        }

    @router.get("/config", response_model=ConfigResponse)
    async def get_config() -> dict[str, Any]:
        """Return the current versioned RFE configuration."""
        config = _load_config()
        return {"status": "ok", "config": config.raw}

    @router.get("/health", response_model=HealthResponse)
    async def health() -> dict[str, str]:
        """RFE health check."""
        config = _load_config()
        return {
            "status": "ok",
            "engine_version": config.engine_version,
            "policy_version": config.policy_version,
        }

    @router.get("/versions", response_model=VersionsResponse)
    async def versions(case_id: str | None = None) -> dict[str, Any]:
        """List past RFE report versions from the .zarc store."""
        config = _load_config()
        prov = get_provenance_store(config)
        versions: list[VersionInfo] = []
        for entry in prov.iter_entries():
            if entry.engine != "rfe" or entry.event != "report_generated":
                continue
            payload = entry.payload
            entry_case_id = payload.get("case_id")
            if case_id is not None and entry_case_id != case_id:
                continue
            versions.append(
                VersionInfo(
                    version_id=str(payload.get("version_id", "")),
                    case_id=str(entry_case_id or ""),
                    report_hash=str(payload.get("report_hash", "")),
                    timestamp_ns=entry.ts_ns,
                    event=entry.event,
                )
            )
        return {"status": "ok", "versions": [v.model_dump() for v in versions]}

    @router.get("/sensitivity")
    async def sensitivity_report(manifest_hash: str = Query(...)) -> dict[str, Any]:
        """Recompute the report under ±50% half-life variations for a stored manifest."""
        config = _load_config()
        manifest = get_manifest_by_hash(config, manifest_hash)
        if manifest is None:
            raise HTTPException(status_code=404, detail="manifest_not_found")
        return {
            "status": "ok",
            **generate_sensitivity_report(manifest, config.raw),
        }

    return router


router = _build_router()
