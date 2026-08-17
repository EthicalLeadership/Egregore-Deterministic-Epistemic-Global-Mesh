"""Port-based FastAPI routes for the ANCHORUM module.

Mounted under /modules/anchorum. ANCHORUM never touches the filesystem; it
operates through IAnchorumIngestionPort only.
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from asds.application.anchorum_adapter import AnchorumAdapter
from asds.domain.ports import IAnchorumIngestionPort
from modules.anchorum import analysis, chronology, ingestion

router = APIRouter(prefix="/modules/anchorum", tags=["ANCHORUM Module"])


def get_port() -> IAnchorumIngestionPort:
    """Provide the concrete ASDS port adapter to route handlers."""
    data_dir = os.environ.get("ASDS_DATA_DIR")
    return AnchorumAdapter(data_dir=data_dir)


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------
class IngestRequest(BaseModel):
    workspace_id: str = Field(..., min_length=1)
    content_ref: str = Field(..., min_length=1)
    case_id: str | None = None


class AnalyzeRequest(BaseModel):
    evidence_id: str = Field(..., min_length=1)


class ChronologyRequest(BaseModel):
    case_id: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/ingest")
def ingest_document(
    req: IngestRequest,
    port: IAnchorumIngestionPort = Depends(get_port),  # noqa: B008
) -> dict[str, Any]:
    """Drop raw doc → ASDS Evidence → ANCHORUM metadata extraction."""
    evidence = port.create_evidence(
        workspace_id=req.workspace_id,
        content_ref=req.content_ref,
        case_id=req.case_id,
    )
    content = port.get_evidence_content(evidence.id)
    metadata = ingestion.extract_metadata(
        content, filename_hint=evidence.original_filename
    )
    output = port.register_analysis_output(
        evidence.id, "metadata", metadata, ingestion.hash_bytes(content)
    )
    port.emit_bus_event(
        "anchorum.evidence_ingested",
        {"evidence_id": evidence.id, "case_id": req.case_id},
    )
    return {
        "evidence": {
            "id": evidence.id,
            "workspace_id": evidence.workspace_id,
            "case_id": evidence.case_id,
            "content_ref": evidence.content_ref,
            "container_type": evidence.container_type,
            "mime_type": evidence.mime_type,
        },
        "output": {
            "id": output.id,
            "analysis_type": output.analysis_type,
            "version_number": output.version_number,
            "content_ref": output.content_ref,
        },
    }


@router.post("/analyze")
def analyze_evidence(
    req: AnalyzeRequest,
    port: IAnchorumIngestionPort = Depends(get_port),  # noqa: B008
) -> dict[str, Any]:
    """Run manipulation detection on existing Evidence."""
    content = port.get_evidence_content(req.evidence_id)
    report = analysis.detect_manipulation(
        content, evidence_id=req.evidence_id
    )
    output = port.register_analysis_output(
        req.evidence_id, "manipulation", report, ingestion.hash_bytes(content)
    )
    port.emit_bus_event(
        "anchorum.evidence_analyzed",
        {"evidence_id": req.evidence_id, "score": report.get("score")},
    )
    return {
        "evidence_id": req.evidence_id,
        "report": report,
        "output": {
            "id": output.id,
            "analysis_type": output.analysis_type,
            "version_number": output.version_number,
            "content_ref": output.content_ref,
        },
    }


@router.get("/chronology/{case_id}")
def get_chronology(
    case_id: str,
    port: IAnchorumIngestionPort = Depends(get_port),  # noqa: B008
) -> dict[str, Any]:
    """Build timeline from all Evidence in a Case."""
    evidence_list = port.get_case_evidence(case_id)
    timeline = chronology.build_chronology(evidence_list, port)

    # Chronology is a case-level output. Register it against the first evidence
    # in the case so it has a workspace home; if the case is empty, skip output
    # registration and just return the timeline.
    output_info: dict[str, Any] | None = None
    if evidence_list:
        anchor_evidence_id = evidence_list[0].id
        timeline_json = json.dumps(timeline, ensure_ascii=False, sort_keys=True)
        output = port.register_analysis_output(
            anchor_evidence_id,
            "chronology",
            timeline,
            ingestion.hash_bytes(timeline_json.encode()),
        )
        output_info = {
            "id": output.id,
            "analysis_type": output.analysis_type,
            "version_number": output.version_number,
            "content_ref": output.content_ref,
        }

    port.emit_bus_event(
        "anchorum.chronology_built",
        {"case_id": case_id, "event_count": timeline.get("event_count", 0)},
    )
    return {
        "case_id": case_id,
        "timeline": timeline,
        "output": output_info,
    }
