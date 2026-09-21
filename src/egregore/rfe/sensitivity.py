# epistemic marker: provenance / auditability
"""
Sensitivity analysis for the Reproducible Fusion Engine.
Exposes how decay parameter variations affect report conclusions.
"""

from __future__ import annotations

import copy
from typing import Any

from egregore.rfe.engine import reproducible_fusion


def _selected_stream_ids(report: dict[str, Any], min_confidence: float) -> set[str]:
    """Return stream ids that are accepted in the report decision log."""
    scored = report.get("report", {}).get("decision_log", {}).get("scored_streams", [])
    return {
        s["stream_id"]
        for s in scored
        if s.get("composite_score", 0.0) >= min_confidence
    }


def generate_sensitivity_report(manifest: dict, config: dict) -> dict:
    """
    Recomputes the report under ±50% half-life variations for all streams
    with finite decay. Returns which conclusions change.
    """
    base_report = reproducible_fusion(manifest, config)
    streams_with_decay = [
        s
        for s in manifest["streams"]
        if s.get("decay", {}).get("method") != "unbounded"
    ]

    if not streams_with_decay:
        return {
            "base_report_hash": base_report["report_hash"],
            "sensitivity": {"verdict": "no_finite_decay_streams", "changes": []},
        }

    min_confidence = float(config.get("min_confidence", 0.5))
    base_selected = _selected_stream_ids(base_report, min_confidence)
    changes = []

    for stream in streams_with_decay:
        orig_half_life = stream["decay"]["half_life_hours"]
        for factor in (0.5, 1.5):
            variant_manifest = copy.deepcopy(manifest)
            for s in variant_manifest["streams"]:
                if s["stream_id"] == stream["stream_id"]:
                    s["decay"]["half_life_hours"] = orig_half_life * factor
                    break
            variant_report = reproducible_fusion(variant_manifest, config)
            variant_selected = _selected_stream_ids(variant_report, min_confidence)

            added = variant_selected - base_selected
            removed = base_selected - variant_selected
            if added or removed:
                changes.append(
                    {
                        "stream_id": stream["stream_id"],
                        "variation": f"{factor}x half-life ({orig_half_life * factor:.1f}h)",
                        "added": sorted(added),
                        "removed": sorted(removed),
                    }
                )

    return {
        "base_report_hash": base_report["report_hash"],
        "sensitivity": {
            "verdict": "changes_detected" if changes else "stable",
            "changes": changes,
        },
    }
