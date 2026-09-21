"""
ANCHORUM Narrative Trace & Demolition Pipeline
Package: anchorum.pipeline.narrative_trace

Self-describing contract: Every module exposes its input schema, output schema,
and processing guarantees via __doc__ and typed dataclasses. The pipeline is
auditable end-to-end: every transformation is logged, every claim is tagged with
epistemic grounding (FACT | DERIVED | MODEL | UNCERTAIN).

Usage:
    from anchorum.pipeline.narrative_trace import NarrativeTracer
    tracer = NarrativeTracer(vault_path="/data/molson_vault")
    timeline = tracer.trace(
        communications_glob="/data/molson/source/*",
        evidence_glob="/data/molson/evidence/*",
        output_path="/data/molson/demolition_timeline.md"
    )
"""

__version__ = "0.6.0-phase1"
__all__ = [
    "NarrativeTracer",
    "CommunicationRecord",
    "EvidenceRecord",
    "DemolitionTimeline",
    "TimelineEntry",
    "EpistemicTag",
    "BurdenShiftTag",
    "Severity",
]

from .analysis.tracer import NarrativeTracer
from .analysis.models import (
    CommunicationRecord,
    EvidenceRecord,
    DemolitionTimeline,
    TimelineEntry,
    EpistemicTag,
    BurdenShiftTag,
    Severity,
)

