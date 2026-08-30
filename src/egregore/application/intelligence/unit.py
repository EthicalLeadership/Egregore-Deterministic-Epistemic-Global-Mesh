"""Intelligence Unit orchestrator.

Runs the collector and counter-intel agents sequentially, returns combined output.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from egregore.application.agents.base import AgentContext, AgentResult
from egregore.application.intelligence.collector import IntelligenceCollectorAgent
from egregore.application.intelligence.counter_intel import CounterIntelAgent
from egregore.domain.artifact.access import AgentArtifactAccessor


class IntelligenceUnit:
    """Coordinates intelligence collection and counter-intelligence analysis."""

    def __init__(self, accessor: AgentArtifactAccessor):
        self.collector = IntelligenceCollectorAgent(accessor)
        self.counter = CounterIntelAgent(accessor)

    def run(self, matter_id: str, raw_inputs: Dict[str, Any]) -> Tuple[AgentResult, AgentResult]:
        """Run collection then counter-intel; return both results."""
        ctx = AgentContext(matter_id=matter_id, raw_inputs=raw_inputs)
        collection_result = self.collector.run(ctx)

        # Pass the intelligence report to counter-intel
        raw_inputs["intelligence_report"] = collection_result.findings.get("intel_report")
        counter_ctx = AgentContext(matter_id=matter_id, raw_inputs=raw_inputs)
        counter_result = self.counter.run(counter_ctx)

        return collection_result, counter_result
