"""AssuranceEngine – runs controls over EpistemicGraph.

Controls are functions that take an EpistemicGraph and return a
ControlResult. The engine runs them and aggregates the results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List

from egregore.domain.epistemic import EpistemicGraph, EpistemicStatus
from .control_models import ControlResult, ControlStatus, AssuranceReport


ControlFunction = Callable[[EpistemicGraph], ControlResult]


# --- Basic Controls -------------------------------------------------

def control_referential_integrity(graph: EpistemicGraph) -> ControlResult:
    """Check that all references in propositions/contradictions resolve."""
    violations = graph.validate_referential_integrity()
    if violations:
        return ControlResult(
            control_id="E06",
            name="Referential integrity",
            status=ControlStatus.BLOCKER,
            message=f"Found {len(violations)} reference violation(s).",
            details={"violations": violations},
            evidence_ids=tuple(),
        )
    return ControlResult(
        control_id="E06",
        name="Referential integrity",
        status=ControlStatus.PASS,
        message="All references resolve correctly.",
        evidence_ids=tuple(),
    )


def control_no_orphan_propositions(graph: EpistemicGraph) -> ControlResult:
    """Every material proposition must have evidence or inference chain."""
    orphans = graph.get_orphaned_propositions()
    if orphans:
        return ControlResult(
            control_id="E05",
            name="No orphan propositions",
            status=ControlStatus.BLOCKER,
            message=f"Found {len(orphans)} orphaned proposition(s).",
            details={"orphans": [p.id for p in orphans]},
            evidence_ids=tuple(),
        )
    return ControlResult(
        control_id="E05",
        name="No orphan propositions",
        status=ControlStatus.PASS,
        message="No orphaned propositions.",
        evidence_ids=tuple(),
    )


def control_no_unsupported_propositions(graph: EpistemicGraph) -> ControlResult:
    """Material propositions must not be UNSUPPORTED (we only have statuses now)."""
    unsupported = [p for p in graph.propositions if p.status == EpistemicStatus.UNSUPPORTED]
    if unsupported:
        return ControlResult(
            control_id="E02",
            name="No unsupported propositions",
            status=ControlStatus.FAIL,
            message=f"Found {len(unsupported)} unsupported proposition(s).",
            details={"unsupported": [p.id for p in unsupported]},
            evidence_ids=tuple(),
        )
    return ControlResult(
        control_id="E02",
        name="No unsupported propositions",
        status=ControlStatus.PASS,
        message="No unsupported propositions.",
        evidence_ids=tuple(),
    )


class AssuranceEngine:
    """Runs a list of control functions and produces an AssuranceReport."""

    def __init__(self, controls: List[ControlFunction] | None = None):
        self.controls = controls or [
            control_referential_integrity,
            control_no_orphan_propositions,
            control_no_unsupported_propositions,
        ]

    def run(self, graph: EpistemicGraph) -> AssuranceReport:
        """Execute all controls and aggregate results."""
        results = []
        for ctrl in self.controls:
            try:
                result = ctrl(graph)
            except Exception as e:
                # A control that raises an error is treated as a blocker
                result = ControlResult(
                    control_id="CTRL_ERR",
                    name=ctrl.__name__,
                    status=ControlStatus.BLOCKER,
                    message=f"Control raised an exception: {e}",
                    details={},
                    evidence_ids=tuple(),
                )
            results.append(result)

        # Determine overall status: BLOCKER if any BLOCKER, FAIL if any FAIL,
        # WARN if any WARN, else PASS
        if any(r.status == ControlStatus.BLOCKER for r in results):
            overall = ControlStatus.BLOCKER
        elif any(r.status == ControlStatus.FAIL for r in results):
            overall = ControlStatus.FAIL
        elif any(r.status == ControlStatus.WARN for r in results):
            overall = ControlStatus.WARN
        else:
            overall = ControlStatus.PASS

        return AssuranceReport(
            case_id=graph.case_id,
            controls=tuple(results),
            timestamp=datetime.now(timezone.utc).isoformat(),
            overall_status=overall,
        )
