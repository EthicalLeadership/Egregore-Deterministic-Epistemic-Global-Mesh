"""Load evidence from an Anchorum case report JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_evidence_from_report(case_id: str, report_dir: Path | None = None) -> dict[str, str]:
    """Extract high-severity findings from a case report into evidence map.

    Returns a dict mapping evidence_id -> description content.
    """
    if report_dir is None:
        report_dir = Path.cwd()
    report_path = report_dir / f"{case_id}_report.json"
    if not report_path.exists():
        # Try other common locations
        for alt in (Path.cwd(), Path.home() / "egregore"):
            candidate = alt / f"{case_id}_report.json"
            if candidate.exists():
                report_path = candidate
                break
        else:
            return {}

    data = json.loads(report_path.read_text(encoding="utf-8"))

    evidence: dict[str, str] = {}
    findings = data.get("high_findings") or data.get("findings") or []
    if isinstance(findings, list):
        for f in findings:
            anomaly_id = f.get("anomaly_id") or f.get("id") or "unknown"
            desc = f.get("description") or f.get("title") or "No description"
            evidence[anomaly_id] = desc

    return evidence
