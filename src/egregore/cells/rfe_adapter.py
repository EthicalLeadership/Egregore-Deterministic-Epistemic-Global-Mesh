"""Adapters for converting cell results into RFE manifests and streams."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from egregore.cells.executor import CellResult
from egregore.cells.models import CellSpec
from egregore.tooling.deterministic_verification import canonical_dumps


def _now() -> str:
    # justification: datetime.UTC typing quirk; stdlib returns str at runtime.
    return datetime.now(UTC).isoformat(timespec="seconds")  # type: ignore[no-any-return]


def _content_claim(result: CellResult, output_format: dict[str, Any]) -> str:
    """Map a cell verdict to an RFE claim polarity."""
    claim_map: dict[str, str] = dict(output_format.get("claim_map", {}))
    default = "positive" if result.verdict == "PASS" else "negative"
    return claim_map.get(result.verdict, default)


def cell_result_to_stream(
    result: CellResult,
    output_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert a single cell result into an RFE evidence stream dict."""
    if output_format is None:
        output_format = {}

    stream_id = f"{result.cell_id}_{uuid.uuid4().hex[:12]}"
    claim = _content_claim(result, output_format)
    content: dict[str, Any] = {
        "claim": claim,
        "subject": result.taxonomy,
        "cell_id": result.cell_id,
        "verdict": result.verdict,
    }
    if isinstance(result.final_output, dict):
        content["text"] = canonical_dumps(result.final_output)
        content["structured_output"] = result.final_output
    else:
        content["text"] = str(result.final_output)

    return {
        "stream_id": stream_id,
        "type": output_format.get("stream_type", "cell_output"),
        "source_tier": result.tier,
        "content": content,
        "confidence": result.confidence,
        "provenance_hash": result.provenance_hash,
        "signature": None,
        "timestamp": _now(),
        "decay": {"method": "unbounded"},
        "severity_impact": 0.6 if claim == "negative" else 0.5,
        "relevance_tags": [t for t in result.taxonomy.split("/") if t],
    }


def build_manifest(
    case_id: str,
    streams: list[dict[str, Any]],
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble RFE-compatible manifest from cell streams."""
    manifest: dict[str, Any] = {
        "case_id": case_id,
        "timestamp": _now(),
        "streams": streams,
    }
    if constraints:
        manifest["constraints"] = constraints
    return manifest


def spec_output_format(spec: CellSpec) -> dict[str, Any]:
    """Return the output_format block from a spec as a plain dict."""
    return spec.output_format.model_dump()
