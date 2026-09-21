"""ANCHORUM Forensic Cell Executor.

Wraps the ANCHORUM batch runner for Egregore cell-protocol execution.
"""

from __future__ import annotations

import dataclasses
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from egregore.shared.canonical import canonical_dumps, canonical_load_file


@dataclasses.dataclass
class CellContext:
    """Minimal execution context supplied by the Egregore cell runner."""

    work_dir: str | Path
    logs: list[str] = dataclasses.field(default_factory=list)


def run_forensic_analysis(
    context: CellContext,
    input_path: str | Path,
    case_id: str,
    operator: str = "system",
    llm_model_id: str | None = None,
) -> dict[str, Any]:
    """Execute ANCHORUM forensic analysis as a Egregore cell.

    Args:
        context: Cell execution context (work directory and log collector).
        input_path: Directory containing evidence artifacts.
        case_id: Case identifier for provenance.
        operator: Operator identifier.
        llm_model_id: Optional Egregore model ID for LLM-powered case narrative.

    Returns:
        A dict describing the investigation result, metrics, and output paths.

    """
    from anchorum.forensic.core.batch_runner import run_batch

    input_path = Path(input_path)
    work_dir = Path(context.work_dir)
    output_dir = work_dir / "anchorum_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{case_id}_report.json"

    summary = run_batch(
        input_dir=input_path,
        output_path=output_path,
        case_id=case_id,
        operator=operator,
        llm_model_id=llm_model_id,
    )

    # Read the JSON report emitted by the batch runner.
    report_json: dict[str, Any] = {}
    if output_path.exists():
        report_json = canonical_load_file(output_path)

    # Collect per-artifact detail files if present.
    artifact_details: list[dict[str, Any]] = []
    artifacts_dir = output_path.with_suffix(".artifacts")
    if artifacts_dir.exists():
        for detail_file in sorted(artifacts_dir.glob("*.json")):
            artifact_details.append(canonical_load_file(detail_file))

    # Determine the highest severity finding for RFE claim polarity.
    highest_severity = "none"
    for level in ("critical", "high", "medium", "low"):
        key = f"{level}_findings"
        if isinstance(report_json.get(key), list) and len(report_json[key]) > 0:
            highest_severity = level
            break

    return {
        "verdict": "PASS" if highest_severity in {"none", "low", "info"} else "FAIL",
        "highest_severity": highest_severity,
        "summary": summary,
        "report": report_json,
        "artifact_details": artifact_details,
        "output_path": str(output_path),
        "case_id": case_id,
        "executed_at": datetime.now(UTC).isoformat(),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: executor.py <input_path> <case_id> [operator] [llm_model_id]")
        sys.exit(1)

    ctx = CellContext(work_dir=tempfile.mkdtemp(prefix="anchorum_"))
    result = run_forensic_analysis(
        ctx,
        input_path=sys.argv[1],
        case_id=sys.argv[2],
        operator=sys.argv[3] if len(sys.argv) > 3 else "cli",
        llm_model_id=sys.argv[4] if len(sys.argv) > 4 else None,
    )
    print(canonical_dumps(result["summary"]))
