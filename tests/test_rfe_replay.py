"""Idempotency replay test for the Reproducible Fusion Engine."""

from __future__ import annotations

from typing import Any

import pytest

from egregore.rfe.config import load_rfe_config
from egregore.rfe.engine import reproducible_fusion

pytestmark = [pytest.mark.redteam]


def _minimal_manifest() -> dict[str, Any]:
    return {
        "case_id": "case_replay_001",
        "timestamp": "2026-06-29T00:00:00+00:00",
        "streams": [
            {
                "stream_id": "stream_t1_001",
                "type": "court_ruling",
                "source_tier": 1,
                "content": {
                    "claim": "positive",
                    "subject": "liability",
                    "text": "Court found liability established.",
                },
                "confidence": 0.95,
                "provenance_hash": "abcd1234",
                "signature": None,
                "timestamp": "2026-06-28T12:00:00+00:00",
                "decay": {
                    "method": "exponential",
                    "half_life_hours": 720,
                    "justification": "Court rulings retain authority but decay slowly.",
                },
                "severity_impact": 0.9,
                "relevance_tags": ["liability"],
            },
            {
                "stream_id": "stream_t3_001",
                "type": "analyst_report",
                "source_tier": 3,
                "content": {
                    "claim": "negative",
                    "subject": "liability",
                    "text": "Analyst notes mitigating evidence.",
                },
                "confidence": 0.7,
                "provenance_hash": "efgh5678",
                "signature": None,
                "timestamp": "2026-06-28T14:00:00+00:00",
                "decay": {
                    "method": "exponential",
                    "half_life_hours": 168,
                    "justification": "Analyst reports are perishable.",
                },
                "severity_impact": 0.5,
                "relevance_tags": ["liability"],
            },
        ],
        "constraints": {
            "max_pages": 20,
            "required_sections": [
                "summary",
                "timeline",
                "obstruction_analysis",
                "conclusion",
            ],
            "output_format": "pdf-a-1b",
            "language": "en",
        },
    }


def test_reproducible_fusion_idempotency() -> None:
    """Running the same manifest twice must yield identical hashes."""
    config = load_rfe_config()
    manifest = _minimal_manifest()

    first = reproducible_fusion(manifest, config)
    second = reproducible_fusion(manifest, config)

    assert first["report_hash"] == second["report_hash"]
    assert first["decision_log_hash"] == second["decision_log_hash"]
    assert first["version_id"] == second["version_id"]


def test_sensitivity_appendix_present_for_finite_decay() -> None:
    """A manifest with finite-decay streams must include a sensitivity appendix."""
    config = load_rfe_config()
    result = reproducible_fusion(_minimal_manifest(), config)
    sections = {s["name"] for s in result["report"]["sections"]}
    assert "sensitivity_appendix" in sections
    appendix = next(
        s for s in result["report"]["sections"] if s["name"] == "sensitivity_appendix"
    )
    assert appendix["rendered"]
