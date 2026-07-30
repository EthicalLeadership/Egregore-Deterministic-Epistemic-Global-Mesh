"""ANCHORUM frontend-backend bridge.

Provides async batch triggers, case listing, report/timeline/anomaly retrieval,
and a Stage-4-compatible ``/ingest`` endpoint for the ANCHORUM forensic pipeline.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from egregore.cells.executor import CellExecutor, CellResult
from egregore.cells.registry import CellRegistry
from egregore.cells.rfe_adapter import (
    build_manifest,
    cell_result_to_stream,
    spec_output_format,
)
from egregore.rfe.engine import reproducible_fusion
from egregore.shared.canonical import canonical_dumps, canonical_loads

logger = logging.getLogger("egregore.anchorum")

router = APIRouter(prefix="/api/v1/anchorum", tags=["ANCHORUM"])


# ---------------------------------------------------------------------------
# Simple in-memory rate limiter for the public /ingest endpoint.
# Production should replace this with a Redis-backed or reverse-proxy limiter.
# ---------------------------------------------------------------------------
class _IngestRateLimiter:
    """Token-bucket rate limiter keyed by source IP."""

    def __init__(self, rate: float = 0.5, capacity: int = 10):
        self.rate = rate  # tokens per second
        self.capacity = capacity
        self._buckets: dict[str, tuple[float, float]] = {}

    def is_allowed(self, key: str) -> bool:
        from time import monotonic

        now = monotonic()
        tokens, last = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + self.rate * (now - last))
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            return True
        self._buckets[key] = (tokens, now)
        return False


_ingest_limiter = _IngestRateLimiter(rate=0.2, capacity=5)

# Standalone ingest router (mounted at the root so the Stage-4 connector can POST
# to ``/ingest``).
ingest_router = APIRouter(tags=["ANCHORUM Ingest"])

# Writable report directory. Defaults inside the Egregore tree so the server
# process can always write, even when sandboxed. Existing Legal Dossier
# summaries are still discoverable via ANCHORUM_READ_ONLY_REPORT_DIRS.
DEFAULT_REPORT_DIR = Path(
    os.environ.get("ANCHORUM_REPORT_DIR", "/opt/egregore/ANCHORUM_reports")
)

# Comma-separated list of additional report directories to search for existing
# summaries (e.g. Legal Dossier's migrated ANCHORUM_reports). These are read-only.
_READ_ONLY_DEFAULT = str(
    Path(os.environ.get("DOSSIER_ROOT", "/opt/egregore/dossier"))
    / "ANCHORUM_reports.migrated_20260629_202848"
)
READ_ONLY_REPORT_DIRS = [
    Path(p.strip())
    for p in os.environ.get("ANCHORUM_READ_ONLY_REPORT_DIRS", _READ_ONLY_DEFAULT).split(
        ","
    )
    if p.strip()
]


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------
class BatchRequest(BaseModel):
    input_path: str = Field(..., min_length=1)
    case_id: str = Field(..., min_length=1)
    operator: str = "web_ui"
    fuse: bool = Field(
        default=False, description="Run RFE fusion over the ANCHORUM stream."
    )
    llm_model_id: str | None = Field(
        default=None,
        description="Optional Egregore model ID for LLM-powered case narrative.",
    )

    @field_validator("input_path")
    @classmethod
    def _input_path_must_exist(cls, value: str) -> str:
        path = Path(value).resolve()
        if not path.exists():
            raise ValueError(f"input_path does not exist: {value}")
        if not path.is_dir():
            raise ValueError(f"input_path must be a directory: {value}")
        return str(path)


class CaseSummary(BaseModel):
    case_id: str
    artifact_count: int
    entity_count: int
    anomaly_count: int
    critical_count: int
    high_count: int
    report_path: str


class IngestionEvent(BaseModel):
    """Loose model for the ANCHORUM Stage-4 IngestionEvent.

    Extra fields are allowed so the connector payload (which evolves) never
    breaks the receipt endpoint.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str = "anchorum"
    purpose: str = "legal_case_relevant"
    source_legality: str = "user_provided"
    scope_status: str = "case_actor"
    entity_kind: str = "email"
    entity_value: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class IngestReceipt(BaseModel):
    receipt_id: str
    epistemic_state: str
    accepted: bool
    event_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _report_dir() -> Path:
    path = Path(DEFAULT_REPORT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _report_path(case_id: str) -> Path:
    """Canonical report path written by the ANCHORUM batch runner."""
    return _report_dir() / f"{case_id}_report.json"


def _all_report_roots() -> list[Path]:
    """Return the writable root followed by any read-only fallback roots."""
    roots = [_report_dir()]
    for root in READ_ONLY_REPORT_DIRS:
        if root.exists() and root not in roots:
            roots.append(root)
    return roots


def _resolve_report_path(case_id: str) -> Path | None:
    """Find an existing report or summary for ``case_id``.

    Searches the writable ANCHORUM report dir first, then any read-only fallback
    dirs configured via ANCHORUM_READ_ONLY_REPORT_DIRS. Keeping writes inside the
    isolated workspace prevents contamination of Legal Dossier.
    """
    candidates: list[Path] = []
    for root in _all_report_roots():
        candidates.extend(
            [
                root / f"{case_id}_report.json",
                root / f"{case_id}_summary.json",
            ]
        )
    candidates.append(_report_dir() / "self_rep_summary.json")
    for root in READ_ONLY_REPORT_DIRS:
        candidates.append(root / "self_rep_summary.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _is_summary_report(raw: dict[str, Any]) -> bool:
    """Detect the flat summary schema produced by the legacy batch extractor."""
    return "critical_findings" not in raw and "artifact_count" in raw


def _normalize_report(raw: dict[str, Any], case_id: str) -> dict[str, Any]:
    """Wrap a flat summary into the canonical ANCHORUM report schema."""
    if not _is_summary_report(raw):
        return raw

    return {
        "case_id": raw.get("case_id") or case_id,
        "report_id": raw.get("report_id", f"summary-{case_id}"),
        "generated_at": raw.get("generated_at"),
        "artifact_count": raw.get("artifact_count", 0),
        "entity_count": raw.get("entity_count", raw.get("unique_email_addresses", 0)),
        "anomaly_count": raw.get("anomaly_count", 0),
        "critical_findings": [],
        "high_findings": [],
        "medium_findings": [],
        "low_findings": [],
        "info_findings": [],
        "master_timeline": [],
        "entity_directory": [],
        "_source_summary": True,
        "_summary": raw,
    }


def _load_report(case_id: str) -> dict[str, Any]:
    report_path = _resolve_report_path(case_id)
    if report_path is None:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")
    raw = cast(dict[str, Any], canonical_loads(report_path.read_text(encoding="utf-8")))
    return _normalize_report(raw, case_id)


def _run_anchorum_batch(
    input_path: str,
    case_id: str,
    operator: str,
    llm_model_id: str | None = None,
) -> dict[str, Any]:
    """Synchronous wrapper that runs ANCHORUM through the cell executor."""
    report_path = _report_path(case_id)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    registry = CellRegistry()
    registry.refresh()
    executor = CellExecutor(registry=registry)

    result = executor.run(
        "anchorum_forensic",
        {
            "input_path": input_path,
            "case_id": case_id,
            "operator": operator,
            "llm_model_id": llm_model_id,
            "work_dir": str(report_path.parent / f"{case_id}_work"),
        },
    )

    # The executor writes the report under its work_dir. Ensure the canonical
    # report path in the frontend-facing directory contains the same content.
    final_output: dict[str, Any] | str | None = result.final_output
    if isinstance(final_output, str):
        try:
            final_output = cast(dict[str, Any], canonical_loads(final_output))
        except Exception:
            final_output = None

    candidates: list[Path] = []
    if isinstance(final_output, dict):
        candidates.append(Path(final_output.get("output_path", "")))
    work_dir = report_path.parent / f"{case_id}_work"
    candidates.append(work_dir / "anchorum_output" / f"{case_id}_report.json")

    source = next(
        (p for p in candidates if p.exists() and p.resolve() != report_path.resolve()),
        None,
    )
    if source:
        report_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    return {
        "status": "completed",
        "case_id": case_id,
        "report_path": str(report_path),
        "result_summary": (
            result.final_output if isinstance(result.final_output, dict) else {}
        ),
    }


def _stage_event(event: IngestionEvent) -> IngestReceipt:
    """Persist a Stage-4 event to the writable report directory and return a receipt."""
    report_dir = _report_dir()
    receipt_id = str(uuid.uuid4())
    staged_path = report_dir / f"{event.event_id}.ingest.json"
    staged_path.write_text(
        canonical_dumps(
            {**event.model_dump(), "receipt_id": receipt_id, "received_at": _now_iso()}
        ),
        encoding="utf-8",
    )
    return IngestReceipt(
        receipt_id=receipt_id,
        epistemic_state="accepted",
        accepted=True,
        event_id=event.event_id,
    )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC).isoformat()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/batch")
def trigger_batch(
    request: BatchRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Trigger an ANCHORUM forensic batch run asynchronously."""
    report_path = _report_path(request.case_id)
    if report_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Case {request.case_id} already exists. Choose a new case_id or delete the existing report.",
        )

    background_tasks.add_task(
        _run_anchorum_batch,
        request.input_path,
        request.case_id,
        request.operator,
        request.llm_model_id,
    )

    return {
        "status": "queued",
        "case_id": request.case_id,
        "output_path": str(report_path),
        "message": "Batch run started in background. Poll /cases/{case_id} for results.",
    }


@router.post("/batch/sync")
def trigger_batch_sync(request: BatchRequest) -> dict[str, Any]:
    """Trigger an ANCHORUM forensic batch run synchronously (for small dirs)."""
    return _run_anchorum_batch(
        request.input_path,
        request.case_id,
        request.operator,
        request.llm_model_id,
    )


@router.get("/cases")
def list_cases() -> list[str]:
    """List all case IDs with generated reports or summaries."""
    cases: set[str] = set()
    for root in _all_report_roots():
        for f in root.glob("*_report.json"):
            cases.add(f.stem.replace("_report", ""))
        for f in root.glob("*_summary.json"):
            cases.add(f.stem.replace("_summary", ""))
        summary = root / "self_rep_summary.json"
        if summary.exists():
            cases.add("self_rep")
    return sorted(cases)


@router.get("/cases/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    """Get the full investigation report for a case."""
    return _load_report(case_id)


@router.get("/cases/{case_id}/anomalies")
def get_anomalies(case_id: str) -> dict[str, Any]:
    """Get only anomaly findings for a case."""
    report = _load_report(case_id)
    return {
        "critical": report.get("critical_findings", []),
        "high": report.get("high_findings", []),
        "medium": report.get("medium_findings", []),
        "low": report.get("low_findings", []),
        "info": report.get("info_findings", []),
    }


@router.get("/cases/{case_id}/timeline")
def get_timeline(case_id: str) -> dict[str, Any]:
    """Get the master timeline for a case."""
    report = _load_report(case_id)
    return {"timeline": report.get("master_timeline", [])}


@router.get("/cases/{case_id}/summary")
def get_summary(case_id: str) -> dict[str, Any]:
    """Get a compact case summary."""
    report = _load_report(case_id)
    return {
        "case_id": report.get("case_id", case_id),
        "report_id": report.get("report_id"),
        "generated_at": report.get("generated_at"),
        "artifact_count": report.get("artifact_count", 0),
        "entity_count": report.get("entity_count", 0),
        "anomaly_count": report.get("anomaly_count", 0),
        "critical_count": len(report.get("critical_findings", [])),
        "high_count": len(report.get("high_findings", [])),
        "medium_count": len(report.get("medium_findings", [])),
        "low_count": len(report.get("low_findings", [])),
    }


@router.post("/batch/fuse")
def trigger_batch_and_fuse(request: BatchRequest) -> dict[str, Any]:
    """Run ANCHORUM and immediately fuse its stream through the RFE."""
    result = _run_anchorum_batch(
        request.input_path,
        request.case_id,
        request.operator,
        request.llm_model_id,
    )
    registry = CellRegistry().refresh()
    spec = registry.get("anchorum_forensic")

    # Build a CellResult from the batch output for RFE fusion.
    summary = result.get("result_summary", {})
    highest = summary.get("highest_severity", "none")
    cell_result = CellResult(
        cell_id="anchorum_forensic",
        cell_type=spec.type,
        tier=spec.tier,
        taxonomy=spec.taxonomy_path(),
        request=request.model_dump(),
        stages={},
        final_output=summary,
        verdict="PASS" if highest in {"none", "low", "info"} else "FAIL",
        confidence=0.9,
        elapsed_ms=0.0,
        provenance_hash="",
    )
    stream = cell_result_to_stream(cell_result, spec_output_format(spec))
    manifest = build_manifest(
        case_id=request.case_id,
        streams=[stream],
    )
    fusion = reproducible_fusion(manifest)
    return {
        "status": "ok",
        "anchorum_result": result,
        "report": fusion.get("report"),
        "report_hash": fusion.get("report_hash"),
        "version_id": fusion.get("version_id"),
    }


# ---------------------------------------------------------------------------
# Stage-4 ingest endpoint (mounted at /ingest via ingest_router)
# ---------------------------------------------------------------------------
@ingest_router.post("/ingest")
def ingest_event(event: IngestionEvent, request: Request) -> IngestReceipt:
    """Accept a Stage-4 IngestionEvent from the ANCHORUM connector.

    Events are staged to the report directory and can be fused into a case
    report by triggering ``POST /api/v1/anchorum/batch`` with the dossier root.

    A lightweight per-source rate limit is applied because this endpoint is
    intentionally public. Production deployments should add source validation
    or a connector token.
    """
    client_key = request.client.host if request.client else "unknown"
    if not _ingest_limiter.is_allowed(client_key):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    try:
        return _stage_event(event)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to stage ingest event %s", event.event_id)
        raise HTTPException(
            status_code=500, detail=f"Ingest staging failed: {exc}"
        ) from exc
