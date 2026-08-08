# epistemic marker: provenance / auditability
"""Deterministic stub tools for AEGIS-HIVE Ω cells.

These tools are placeholders for Phase 0. Each will be replaced by real
implementations as the corresponding cell is built out.
"""

from __future__ import annotations

from typing import Any


def _stage_id(stage: Any) -> str:
    return str(getattr(stage, "stage_id", "unknown"))


def aegis_sensor_stub(stage: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Stub for the telemetry sensor cell."""
    return {
        "verdict": "PASS",
        "output": f"AEGIS sensor stub for {_stage_id(stage)}: OK",
        "events": [],
        "details": {"backend": "auditd_stub", "count": 0},
    }


def aegis_intel_stub(stage: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Stub for the threat-intel fusion cell."""
    return {
        "verdict": "PASS",
        "output": f"AEGIS intel stub for {_stage_id(stage)}: OK",
        "findings": [],
        "details": {"indicators_matched": 0},
    }


def aegis_reasoner_stub(stage: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Stub for the attack-path reasoner cell."""
    return {
        "verdict": "PASS",
        "output": f"AEGIS reasoner stub for {_stage_id(stage)}: OK",
        "actions": [],
        "details": {"paths_scored": 0},
    }


def aegis_actor_stub(stage: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Stub for the autonomous response actor cell."""
    return {
        "verdict": "PASS",
        "output": f"AEGIS actor stub for {_stage_id(stage)}: OK",
        "executed": [],
        "details": {"actions_pending": 0},
    }
