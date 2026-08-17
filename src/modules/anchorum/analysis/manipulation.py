"""Lightweight manipulation/rhetorical anomaly detection on evidence bytes.

This is a port-clean, dependency-light starting point. It does not import
anchorum.forensic.core (which performs direct I/O). Future work can wire in
the obstruction/hidden-layer detectors through the port contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from modules.anchorum.ingestion.filetypes import ContainerType, detect_container


@dataclass(frozen=True)
class ManipulationReport:
    evidence_id: str | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "findings": self.findings,
            "score": self.score,
        }


def _pdf_findings(data: bytes) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    text = data[:524288].decode("latin-1", errors="ignore")

    if "/JavaScript" in text or "/JS" in text:
        findings.append(
            {
                "type": "embedded_javascript",
                "severity": "high",
                "description": "PDF contains JavaScript actions",
            }
        )

    if "/EmbeddedFiles" in text or "/Names" in text:
        findings.append(
            {
                "type": "embedded_files",
                "severity": "medium",
                "description": "PDF contains embedded file streams",
            }
        )

    if re.search(r"/OpenAction\s*<<", text):
        findings.append(
            {
                "type": "open_action",
                "severity": "medium",
                "description": "PDF has an automatic open action",
            }
        )

    # Common redaction/obfuscation markers
    if b"Redact" in data or b"/Redaction" in data:
        findings.append(
            {
                "type": "redaction_annotation",
                "severity": "low",
                "description": "Redaction annotations present",
            }
        )

    return findings


def _email_findings(data: bytes) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    text = data[:262144].decode("utf-8", errors="ignore")

    if "X-Proofpoint" in text or "X-Microsoft-Antispam" in text:
        findings.append(
            {
                "type": "security_gateway",
                "severity": "info",
                "description": "Email passed through security gateways",
            }
        )

    # Detect spoofing-relevant mismatches (very basic)
    from_match = re.search(r"(?i)^From:\s*<?([^>\n]+)", text, re.MULTILINE)
    sender_match = re.search(r"(?i)^Sender:\s*<?([^>\n]+)", text, re.MULTILINE)
    if (
        from_match
        and sender_match
        and from_match.group(1).strip() != sender_match.group(1).strip()
    ):
        findings.append(
            {
                "type": "sender_mismatch",
                "severity": "medium",
                "description": "From and Sender headers differ",
            }
        )

    return findings


def detect_manipulation(
    data: bytes,
    evidence_id: str | None = None,
    filename_hint: str | None = None,
) -> dict[str, Any]:
    """Detect manipulation indicators in raw evidence bytes."""
    container = detect_container(data, filename_hint=filename_hint)

    if container == ContainerType.PDF:
        findings = _pdf_findings(data)
    elif container == ContainerType.EMAIL:
        findings = _email_findings(data)
    else:
        findings = [
            {
                "type": "unsupported_container",
                "severity": "info",
                "description": f"Manipulation detection not yet implemented for {container.value}",
            }
        ]

    severity_score = {"info": 0.1, "low": 0.25, "medium": 0.5, "high": 1.0}
    score = min(
        1.0,
        sum(severity_score.get(f.get("severity", "info"), 0.1) for f in findings),
    )

    report = ManipulationReport(
        evidence_id=evidence_id,
        findings=findings,
        score=score,
    )
    return report.to_dict()
