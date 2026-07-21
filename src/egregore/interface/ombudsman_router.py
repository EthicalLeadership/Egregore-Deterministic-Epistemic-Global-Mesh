"""Ombudsman Router v2 — the University / Guildhall traffic controller.

Maintains a registry of delivered cells, their load indices, and advisory
relationships. Routes incoming requests to the appropriate cell(s), executes
their staged pipelines, and fuses the resulting RFE streams through the
Reproducible Fusion Engine.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from egregore.cells.executor import CellExecutor
from egregore.cells.models import CellSpec
from egregore.cells.registry import CellRegistry
from egregore.cells.rfe_adapter import (
    build_manifest,
    cell_result_to_stream,
    spec_output_format,
)
from egregore.governance.cell_protocol import STAGES, CellProtocolController
from egregore.governance.permissions import Action, PermissionService
from egregore.http_api.http.middleware.api_key_middleware import get_user_identity
from egregore.models.user import UserIdentity
from egregore.rfe.engine import reproducible_fusion
from egregore.rfe.integration.mapper import (
    job_request_to_work_unit,
    work_unit_to_job_response,
)
from egregore.shared.canonical import canonical_dumps

DB_PATH = Path(os.environ.get("BLACKSTAR_REPO_ROOT", "/opt/egregore")) / "rag/cell_protocol.db"

logger = logging.getLogger("egregore.ombudsman")

router = APIRouter(prefix="/api/v1/ombudsman", tags=["ombudsman"])


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------
class RouteRequest(BaseModel):
    taxonomy: str = Field(
        ...,
        description="University/Guildhall path, e.g. university/science/mathematics/calculus",
    )
    payload: dict[str, Any] = Field(default_factory=dict)


class DispatchRequest(BaseModel):
    taxonomy: str = Field(..., description="Target taxonomy path.")
    input: str = Field(
        ..., min_length=1, description="Primary natural-language request."
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    advisory_call: bool = Field(
        default=False, description="Enable advisory calls to linked cells."
    )
    fuse: bool = Field(default=True, description="Run RFE fusion over emitted streams.")
    constraints: dict[str, Any] | None = Field(
        default=None, description="Optional RFE constraints."
    )


class AdvisoryCallRequest(BaseModel):
    caller_cell_id: str
    target_cell_id: str
    input: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class CellRegistryEntry(BaseModel):
    cell_id: str
    taxonomy: str
    endpoint: str
    version: str
    status: str
    load_index: float = 0.0
    capacity: int = 10
    relationships: list[dict[str, str]] = []


class CellLoadUpdate(BaseModel):
    cell_id: str
    load_index: float = Field(..., ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------
_default_registry: CellRegistry | None = None


def _get_registry(request: Request | None = None) -> CellRegistry:
    """Resolve the cell registry from app state or the module-level default."""
    if request is not None:
        reg: CellRegistry | None = getattr(request.app.state, "cell_registry", None)
        if reg is not None:
            return reg

    global _default_registry
    if _default_registry is None:
        _default_registry = CellRegistry()
        _default_registry.refresh()
    return _default_registry


def _get_executor(request: Request | None = None) -> CellExecutor:
    """Resolve a cell executor from app state or create a default."""
    if request is not None:
        exe: CellExecutor | None = getattr(request.app.state, "cell_executor", None)
        if exe is not None:
            return exe
    return CellExecutor(registry=_get_registry(request))


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Existing registry endpoints
# ---------------------------------------------------------------------------
@router.get("/cells")
def list_cells(request: Request) -> dict[str, Any]:
    """Return all registered cells and their current load/status."""
    reg = _get_registry(request)
    ctrl = CellProtocolController()
    bccbp_states = {c.cell_id: c for c in ctrl.list_cells()}

    cells = []
    for spec in reg.list_cells():
        state = bccbp_states.get(spec.cell_id)
        cells.append(
            {
                "cell_id": spec.cell_id,
                "version": spec.version,
                "type": spec.type,
                "tier": spec.tier,
                "taxonomy": spec.taxonomy_path(),
                "owner": spec.owner,
                "current_stage": state.current_stage if state else "unknown",
                "status": state.status if state else "unknown",
                "progress": _progress(state.stage_states) if state else 0.0,
                "load_index": reg.get_load(spec.cell_id),
                "max_load": spec.max_load,
                "advisory_cells": spec.advisory_cells,
            }
        )
    return {"cells": cells}


@router.get("/cells/{cell_id}")
def get_cell(cell_id: str, request: Request) -> dict[str, Any]:
    """Return detailed state for a single cell."""
    reg = _get_registry(request)
    spec = reg.get(cell_id)
    ctrl = CellProtocolController()
    state = ctrl.get_state(cell_id)
    return {
        "cell_id": state.cell_id,
        "version": state.version,
        "type": spec.type,
        "tier": spec.tier,
        "taxonomy": state.taxonomy,
        "owner": state.owner,
        "current_stage": state.current_stage,
        "status": state.status,
        "stage_states": state.stage_states,
        "progress": _progress(state.stage_states),
        "load_index": reg.get_load(cell_id),
        "max_load": spec.max_load,
        "advisory_cells": spec.advisory_cells,
        "dependencies": spec.dependencies,
    }


@router.get("/load")
def list_load(request: Request) -> dict[str, Any]:
    """Return current load index for every registered cell."""
    reg = _get_registry(request)
    return {
        "loads": {
            spec.cell_id: {
                "current": reg.get_load(spec.cell_id),
                "max": spec.max_load,
            }
            for spec in reg.list_cells()
        }
    }


@router.get("/route/{taxonomy:path}")
def route_to_cell(taxonomy: str, request: Request) -> dict[str, Any]:
    """Find the best available cell for a given taxonomy path."""
    reg = _get_registry(request)
    ctrl = CellProtocolController()
    candidates = [
        spec
        for spec in reg.find_by_taxonomy(taxonomy)
        if _is_available(ctrl, spec.cell_id)
    ]
    if not candidates:
        raise HTTPException(
            status_code=404, detail=f"No available cell found for taxonomy: {taxonomy}"
        )

    chosen = reg.select_least_loaded(candidates)
    if chosen is None:
        raise HTTPException(
            status_code=503,
            detail=f"All cells for taxonomy '{taxonomy}' are at capacity",
        )

    return {
        "taxonomy": taxonomy,
        "selected_cell": chosen.cell_id,
        "version": chosen.version,
        "endpoint": _derive_endpoint(chosen.cell_id),
        "status": ctrl.get_state(chosen.cell_id).status,
        "load_index": reg.get_load(chosen.cell_id),
    }


@router.post("/cells/{cell_id}/load")
def update_load(
    cell_id: str, update: CellLoadUpdate, request: Request
) -> dict[str, Any]:
    """Update a cell's load index (called by the cell or monitor)."""
    reg = _get_registry(request)
    if cell_id not in reg.specs:
        raise HTTPException(status_code=404, detail=f"Cell not registered: {cell_id}")
    reg.set_load(cell_id, update.load_index)

    ctrl = CellProtocolController()
    state = ctrl.get_state(cell_id)
    state.stage_states.setdefault("deliver", {})
    state.stage_states["deliver"]["load_index"] = update.load_index

    with _connection() as conn:
        conn.execute(
            "UPDATE cells SET stage_states = ?, updated_at = ? WHERE cell_id = ?",
            (canonical_dumps(state.stage_states), time.time(), cell_id),
        )
        conn.commit()

    return {"cell_id": cell_id, "load_index": update.load_index}


@router.get("/university/graph")
def university_graph(request: Request) -> dict[str, Any]:
    """Return the University graph as nodes and edges for dashboard rendering."""
    reg = _get_registry(request)
    ctrl = CellProtocolController()
    bccbp_states = {c.cell_id: c for c in ctrl.list_cells()}

    nodes = []
    edges = []
    for spec in reg.list_cells():
        state = bccbp_states.get(spec.cell_id)
        nodes.append(
            {
                "id": spec.cell_id,
                "label": spec.cell_id,
                "taxonomy": spec.taxonomy_path(),
                "type": spec.type,
                "status": state.status if state else "unknown",
                "stage": state.current_stage if state else "unknown",
                "progress": _progress(state.stage_states) if state else 0.0,
                "tier": spec.tier,
            }
        )
        for advisory in spec.advisory_cells:
            edges.append(
                {"source": spec.cell_id, "target": advisory, "type": "advisory"}
            )
        for dep in spec.dependencies:
            edges.append({"source": spec.cell_id, "target": dep, "type": "dependency"})
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# New v2 dispatch and advisory endpoints
# ---------------------------------------------------------------------------
@router.post("/dispatch")
def dispatch(
    req: DispatchRequest,
    request: Request,
    identity: UserIdentity = Depends(get_user_identity),  # noqa: B008
) -> dict[str, Any]:
    """Route a request to matching cells, execute them, and fuse the streams."""
    reg = _get_registry(request)
    executor = _get_executor(request)

    candidates = reg.find_by_taxonomy(req.taxonomy)
    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"No cells registered for taxonomy: {req.taxonomy}",
        )

    # Select target cells: all matching for now, but cap to avoid overload.
    selected = candidates[:3]
    authorized = [
        spec for spec in selected if _can_write_vertical(identity, spec.cell_id)
    ]
    if not authorized:
        raise HTTPException(
            status_code=403,
            detail="You do not have write access to any matching vertical",
        )
    selected = authorized
    streams: list[dict[str, Any]] = []
    cell_results: list[dict[str, Any]] = []

    for spec in selected:
        if reg.get_load(spec.cell_id) >= spec.max_load:
            continue
        reg.increment_load(spec.cell_id, 0.1)
        try:
            payload = {**req.payload, "input": req.input}
            result = executor.run(spec.cell_id, payload)
            stream = cell_result_to_stream(result, spec_output_format(spec))
            streams.append(stream)
            cell_results.append(_summarize_result(result))

            if req.advisory_call:
                advisory_streams = _run_advisory_calls(
                    executor, reg, spec, result, req.input
                )
                streams.extend(advisory_streams)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Cell %s dispatch failed", spec.cell_id)
            streams.append(_error_stream(spec, str(exc)))

    if not streams:
        raise HTTPException(status_code=503, detail="No cells could produce a stream")

    if not req.fuse:
        return {
            "status": "streams_only",
            "taxonomy": req.taxonomy,
            "streams": streams,
            "cell_results": cell_results,
        }

    manifest = build_manifest(
        case_id=f"omb_{uuid.uuid4().hex[:16]}",
        streams=streams,
        constraints=req.constraints,
    )
    try:
        fusion = reproducible_fusion(manifest)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"RFE fusion failed: {exc}"
        ) from exc

    return {
        "status": "ok",
        "taxonomy": req.taxonomy,
        "streams": streams,
        "cell_results": cell_results,
        "report": fusion.get("report"),
        "report_hash": fusion.get("report_hash"),
        "decision_log_hash": fusion.get("decision_log_hash"),
        "version_id": fusion.get("version_id"),
    }


@router.post("/advisory/call")
def advisory_call(
    req: AdvisoryCallRequest,
    request: Request,
    identity: UserIdentity = Depends(get_user_identity),  # noqa: B008
) -> dict[str, Any]:
    """Let one cell invoke another cell and return the resulting RFE stream."""
    reg = _get_registry(request)
    executor = _get_executor(request)

    caller = reg.get(req.caller_cell_id)
    if req.target_cell_id not in caller.advisory_cells:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Cell '{req.caller_cell_id}' is not configured to call "
                f"'{req.target_cell_id}'. Add it to advisory_cells."
            ),
        )

    target = reg.get(req.target_cell_id)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"Cell not found: {req.target_cell_id}"
        )
    if not _can_write_vertical(identity, target.cell_id):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have write access to vertical {target.cell_id}",
        )
    payload = {"input": req.input, **req.payload}
    result = executor.run(target.cell_id, payload)
    stream = cell_result_to_stream(result, spec_output_format(target))
    return {
        "status": "ok",
        "caller_cell_id": req.caller_cell_id,
        "target_cell_id": req.target_cell_id,
        "stream": stream,
        "result_summary": _summarize_result(result),
    }


async def dispatch_work_unit(
    work_unit: dict, request: Request | None = None
) -> dict[str, Any]:
    """Dispatch a DT1 WorkUnit through the ombudsman cell dispatch path."""
    payload = dict(work_unit.get("payload", {}))
    input_text = str(payload.pop("input", ""))
    req = DispatchRequest(
        taxonomy=str(work_unit.get("task", "")),
        input=input_text,
        payload=payload,
    )
    dispatch_result = dispatch(req, request)
    return work_unit_to_job_response(
        {
            "unit_id": work_unit.get("unit_id"),
            "status": "completed",
            "output": dispatch_result,
            "provenance": work_unit.get("provenance", {}),
        }
    )


@router.post("/dispatch_from_orchestrator")
async def dispatch_from_orchestrator(
    job_request: dict, request: Request
) -> dict[str, Any]:
    """Accept an orchestration-suite JobRequest and dispatch it as a WorkUnit."""
    work_unit = job_request_to_work_unit(job_request)
    result = await dispatch_work_unit(work_unit, request)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_delivered(ctrl: CellProtocolController, cell_id: str) -> bool:
    try:
        return ctrl.get_state(cell_id).status == "delivered"
    except KeyError:
        return False


def _is_available(ctrl: CellProtocolController, cell_id: str) -> bool:
    """A cell is available for routing if it is registered and not failed."""
    try:
        state = ctrl.get_state(cell_id)
    except KeyError:
        return False
    if state.status == "failed":
        return False
    # Require at least the plan stage to be completed.
    return state.stage_states.get("plan", {}).get("status") == "completed"


def _progress(stage_states: dict[str, Any]) -> float:
    completed = sum(
        1 for s in STAGES if stage_states.get(s, {}).get("status") == "completed"
    )
    return round(completed / len(STAGES), 2)


def _derive_endpoint(cell_id: str) -> str:
    return f"/api/v1/cells/{cell_id}/run"


def _can_write_vertical(identity: UserIdentity, cell_id: str) -> bool:
    return PermissionService().can(identity, Action.VERTICAL_WRITE, cell_id).ok


def _can_read_vertical(identity: UserIdentity, cell_id: str) -> bool:
    return PermissionService().can(identity, Action.VERTICAL_READ, cell_id).ok


def _summarize_result(result: Any) -> dict[str, Any]:
    return {
        "cell_id": result.cell_id,
        "verdict": result.verdict,
        "confidence": result.confidence,
        "elapsed_ms": result.elapsed_ms,
        "provenance_hash": result.provenance_hash,
    }


def _run_advisory_calls(
    executor: CellExecutor,
    reg: CellRegistry,
    caller_spec: CellSpec,
    caller_result: Any,
    original_input: str,
) -> list[dict[str, Any]]:
    """Invoke advisory cells linked to the caller and return their streams."""
    advisory_streams: list[dict[str, Any]] = []
    for advisory_id in caller_spec.advisory_cells:
        try:
            advisory_spec = reg.get(advisory_id)
            payload = {
                "input": f"Advisory request from {caller_spec.cell_id}: {original_input}",
                "caller_cell": caller_spec.cell_id,
                "caller_output": caller_result.final_output,
            }
            result = executor.run(advisory_id, payload)
            advisory_streams.append(
                cell_result_to_stream(result, spec_output_format(advisory_spec))
            )
            reg.increment_load(advisory_id, 0.05)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Advisory call %s -> %s failed: %s",
                caller_spec.cell_id,
                advisory_id,
                exc,
            )
    return advisory_streams


def _error_stream(spec: CellSpec, detail: str) -> dict[str, Any]:
    return {
        "stream_id": f"{spec.cell_id}_error_{uuid.uuid4().hex[:8]}",
        "type": "cell_error",
        "source_tier": spec.tier,
        "content": {
            "claim": "negative",
            "subject": spec.taxonomy_path(),
            "text": f"Cell execution failed: {detail}",
            "cell_id": spec.cell_id,
        },
        "confidence": 0.0,
        "provenance_hash": "",
        "signature": None,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "decay": {"method": "unbounded"},
        "severity_impact": 0.8,
        "relevance_tags": [t for t in spec.taxonomy_path().split("/") if t],
    }
