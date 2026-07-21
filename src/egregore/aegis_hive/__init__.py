"""AEGIS-HIVE Ω — Autonomous Cognitive Defense Mesh for Egregore.

This package implements the AEGIS-HIVE Ω defensive subsystem as a family of
governed Egregore cells. It reuses Egregore's provenance, governance, RFE,
and multi-agent coordination infrastructure.

Phase 0 scope: shared schemas, ports, stub tools, and cell specifications.
"""

from __future__ import annotations

from egregore.aegis_hive.tools import (
    aegis_actor_stub,
    aegis_intel_stub,
    aegis_reasoner_stub,
    aegis_sensor_stub,
)
from egregore.cells.tools import register_tool

__all__ = [
    "aegis_sensor_stub",
    "aegis_intel_stub",
    "aegis_reasoner_stub",
    "aegis_actor_stub",
]


def _register_aegis_tools() -> None:
    """Register AEGIS-HIVE deterministic tools with the Egregore cell executor."""
    register_tool("aegis_sensor_stub", aegis_sensor_stub)
    register_tool("aegis_intel_stub", aegis_intel_stub)
    register_tool("aegis_reasoner_stub", aegis_reasoner_stub)
    register_tool("aegis_actor_stub", aegis_actor_stub)


_register_aegis_tools()
