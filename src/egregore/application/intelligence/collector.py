"""Intelligence Collector Agent.

Reads secured artifacts and produces structured intelligence findings.
Prefix: [INTEL]
Non-contaminating: outputs are advisory only, never altering operational layers.
"""

from __future__ import annotations

from typing import Any, List, Dict
from datetime import datetime, timezone

from egregore.application.agents.base import BaseAgent, AgentContext, AgentResult
from egregore.application.intelligence.models import (
    SourceDescriptor,
    InformationRequirement,
    IntelFinding,
    IntelligenceReport,
)
from egregore.domain.artifact.access import AgentArtifactAccessor
from egregore.domain.artifact.store import ArtifactStore


class IntelligenceCollectorAgent(BaseAgent):
    """Collects information from secured artifacts and produces intelligence findings."""

    agent_id = "intel_collector"
    version = "0.1.0"

    def __init__(self, accessor: AgentArtifactAccessor, **kwargs: Any):
        super().__init__(**kwargs)
        self.accessor = accessor

    def run(self, context: AgentContext) -> AgentResult:
        """Gather available information and build an intelligence report.

        The context.raw_inputs may contain:
            - artifact_ids: list[str] to inspect
            - external_sources: dict of source_id -> description/type
            - requirements: list of strings
        """
        artifact_ids = context.raw_inputs.get("artifact_ids", [])
        external_sources = context.raw_inputs.get("external_sources", {})
        requirement_descs = context.raw_inputs.get("requirements", [])

        # Build source descriptors
        sources: List[SourceDescriptor] = []
        findings: List[IntelFinding] = []

        for art_id in artifact_ids:
            try:
                art = self.accessor.read(art_id)
                # We cannot access content directly; just metadata
                sources.append(
                    SourceDescriptor(
                        source_id=art.id,
                        type="secured_artifact",
                        grade="A",  # because hash verified
                        date=art.created_at,
                        provenance={"artifact_hash": art.content_hash},
                    )
                )
                findings.append(
                    IntelFinding(
                        id=f"F-{art.id[:8]}",
                        text=f"Secured artifact {art.id} is available and hash-verified.",
                        confidence="CERTAIN",
                        source_ids=(art.id,),
                        temporal_weight=self._temporal_weight(art.created_at),
                    )
                )
            except KeyError:
                findings.append(
                    IntelFinding(
                        id=f"F-MISSING-{art_id[:8]}",
                        text=f"Artifact {art_id} is referenced but not available.",
                        confidence="MISSING",
                        source_ids=(art_id,),
                    )
                )

        # External sources
        for src_id, desc in external_sources.items():
            sources.append(
                SourceDescriptor(
                    source_id=src_id,
                    type=desc.get("type", "external"),
                    grade=desc.get("grade", "U"),
                    date=desc.get("date"),
                )
            )
            findings.append(
                IntelFinding(
                    id=f"F-EXT-{src_id[:8]}",
                    text=desc.get("summary", f"External source {src_id}"),
                    confidence=desc.get("confidence", "UNCERTAIN"),
                    source_ids=(src_id,),
                )
            )

        # Information requirements
        requirements = []
        for i, desc in enumerate(requirement_descs, start=1):
            requirements.append(
                InformationRequirement(id=f"IR-{i:03d}", description=desc, priority=i)
            )

        report = IntelligenceReport(
            matter_id=context.matter_id,
            requirements=tuple(requirements),
            sources=tuple(sources),
            findings=tuple(findings),
            summary=f"Collected {len(findings)} findings from {len(sources)} sources.",
        )

        return AgentResult(
            agent_id=self.agent_id,
            run_id=f"{context.matter_id}-intel",
            findings={"intel_report": report},
            raw_anchorum_outputs={},
        )

    def _temporal_weight(self, artifact_date: datetime | None) -> str:
        if artifact_date is None:
            return "STALE"
        age = (datetime.now(timezone.utc) - artifact_date).days
        if age <= 30:
            return "RECENT"
        elif age <= 180:
            return "MODERATE"
        else:
            return "STALE"
