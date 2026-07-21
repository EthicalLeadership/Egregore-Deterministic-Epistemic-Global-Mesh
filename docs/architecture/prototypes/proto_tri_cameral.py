#!/usr/bin/env python3
"""
TriCameralCouncil — Standalone Study Prototype
Based on: docs/architecture/tri_cameral_council_design.md

Run: python docs/architecture/prototypes/proto_tri_cameral.py
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence


class Chamber(str, Enum):
    UNIVERSITY = "university"
    GUILD = "guild"
    INVESTIGATION = "investigation"


class Verdict(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class CellSpec:
    cell_id: str
    chamber: Chamber
    capability: str
    trust_score: float = 1.0


@dataclass(frozen=True)
class RequestForEvaluation:
    rfe_id: str
    question: str
    context: Mapping[str, Any]
    timestamp_ns: int


@dataclass(frozen=True)
class CellOutput:
    cell_id: str
    chamber: Chamber
    confidence: float
    answer: str
    rationale: str
    provenance_hash: str


@dataclass
class Deliberation:
    rfe_id: str
    deliberation_id: str
    selected_cells: Sequence[CellSpec]
    outputs: list[CellOutput] = field(default_factory=list)
    verdict: Optional[Verdict] = None
    chamber_scores: dict[Chamber, float] = field(default_factory=dict)
    closed_at_ns: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value if self.verdict else None
        d["chamber_scores"] = {c.value: v for c, v in self.chamber_scores.items()}
        d["selected_cells"] = [asdict(c) for c in self.selected_cells]
        d["outputs"] = [asdict(o) for o in self.outputs]
        return d


class CellRegistryPort(Protocol):
    def cells_for(self, chamber: Chamber) -> Sequence[CellSpec]: ...


class CellExecutorPort(Protocol):
    def execute(self, cell: CellSpec, rfe: RequestForEvaluation) -> CellOutput: ...


class FusionPort(Protocol):
    def fuse(self, outputs: Sequence[CellOutput]) -> Mapping[str, Any]: ...


class ZarcJournalPort(Protocol):
    def append(self, event_type: str, payload: Mapping[str, object]) -> str: ...


class InMemoryCellRegistry:
    def __init__(self, cells: Sequence[CellSpec]):
        self._cells = list(cells)

    def cells_for(self, chamber: Chamber) -> Sequence[CellSpec]:
        return [c for c in self._cells if c.chamber == chamber]


class SimulatedCellExecutor:
    def execute(self, cell: CellSpec, rfe: RequestForEvaluation) -> CellOutput:
        answer_text = f"<{cell.chamber.value}/{cell.cell_id}> analyzed '{rfe.question}'"
        payload = json.dumps({
            "cell_id": cell.cell_id,
            "rfe_id": rfe.rfe_id,
            "answer": answer_text,
            "timestamp_ns": rfe.timestamp_ns,
        }, sort_keys=True).encode("utf-8")
        return CellOutput(
            cell_id=cell.cell_id,
            chamber=cell.chamber,
            confidence=0.75 + (cell.trust_score * 0.20),
            answer=answer_text,
            rationale=f"Based on {cell.capability} reasoning.",
            provenance_hash=hashlib.sha256(payload).hexdigest(),
        )


class ReproducibleFusion:
    def fuse(self, outputs: Sequence[CellOutput]) -> Mapping[str, Any]:
        by_chamber: dict[Chamber, list[CellOutput]] = {}
        for o in outputs:
            by_chamber.setdefault(o.chamber, []).append(o)
        chamber_scores: dict[str, float] = {}
        for chamber, outs in by_chamber.items():
            if not outs:
                continue
            avg_conf = sum(o.confidence for o in outs) / len(outs)
            agreement = self._agreement(outs)
            chamber_scores[chamber.value] = avg_conf * agreement
        return {"chamber_scores": chamber_scores, "output_count": len(outputs)}

    def _agreement(self, outputs: Sequence[CellOutput]) -> float:
        if len(outputs) <= 1:
            return 1.0
        answers = [o.answer for o in outputs]
        identical = sum(1 for a in answers if a == answers[0])
        return identical / len(answers)


class InMemoryZarcJournal:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def append(self, event_type: str, payload: Mapping[str, object]) -> str:
        event = {"event_type": event_type, "payload": dict(payload)}
        self.events.append(event)
        return "sha256-dummy"


class TriCameralCouncil:
    def __init__(
        self,
        cell_registry: CellRegistryPort,
        cell_executor: CellExecutorPort,
        fusion: FusionPort,
        zarc_journal: ZarcJournalPort,
        required_chambers: int = 2,
        confidence_threshold: float = 0.70,
    ):
        self.cell_registry = cell_registry
        self.cell_executor = cell_executor
        self.fusion = fusion
        self.zarc_journal = zarc_journal
        self.required_chambers = required_chambers
        self.confidence_threshold = confidence_threshold
        self._deliberations: dict[str, Deliberation] = {}

    def deliberate(self, rfe: RequestForEvaluation, chamber_filter: Optional[Sequence[Chamber]] = None) -> Deliberation:
        deliberation_id = f"delib-{rfe.rfe_id}-{time.time_ns()}"
        chambers = list(Chamber)
        if chamber_filter:
            chambers = [c for c in chambers if c in chamber_filter]
        selected: list[CellSpec] = []
        for chamber in chambers:
            candidates = self.cell_registry.cells_for(chamber)
            if candidates:
                selected.append(max(candidates, key=lambda c: c.trust_score))
        deliberation = Deliberation(rfe_id=rfe.rfe_id, deliberation_id=deliberation_id, selected_cells=selected)
        self._deliberations[deliberation_id] = deliberation
        self.zarc_journal.append(
            "council_deliberation_opened",
            {"deliberation_id": deliberation_id, "rfe_id": rfe.rfe_id, "chambers": [c.value for c in chambers]},
        )
        for cell in selected:
            output = self.cell_executor.execute(cell, rfe)
            deliberation.outputs.append(output)
            self.zarc_journal.append(
                "council_cell_output",
                {"deliberation_id": deliberation_id, "cell_id": cell.cell_id, "provenance_hash": output.provenance_hash},
            )
        fusion_result = self.fusion.fuse(deliberation.outputs)
        deliberation.chamber_scores = {Chamber(k): v for k, v in fusion_result["chamber_scores"].items()}
        deliberation.verdict = self._compute_verdict(deliberation.chamber_scores)
        deliberation.closed_at_ns = time.time_ns()
        self.zarc_journal.append(
            "council_deliberation_closed",
            deliberation.to_dict(),
        )
        return deliberation

    def get_deliberation(self, deliberation_id: str) -> Optional[Deliberation]:
        return self._deliberations.get(deliberation_id)

    def _compute_verdict(self, chamber_scores: Mapping[Chamber, float]) -> Verdict:
        approving = [c for c, score in chamber_scores.items() if score >= self.confidence_threshold]
        if len(approving) >= self.required_chambers:
            return Verdict.APPROVED
        if len(chamber_scores) >= self.required_chambers and len(approving) == 0:
            return Verdict.REJECTED
        return Verdict.INCONCLUSIVE


def demo():
    print("=" * 60)
    print("TriCameralCouncil Prototype")
    print("=" * 60)
    print()

    cells = [
        CellSpec("u-ethics", Chamber.UNIVERSITY, "ethical_reasoning", 0.95),
        CellSpec("u-policy", Chamber.UNIVERSITY, "policy_analysis", 0.85),
        CellSpec("g-ops", Chamber.GUILD, "operational_risk", 0.90),
        CellSpec("g-craft", Chamber.GUILD, "craft_quality", 0.80),
        CellSpec("i-audit", Chamber.INVESTIGATION, "forensic_audit", 0.92),
        CellSpec("i-threat", Chamber.INVESTIGATION, "threat_modeling", 0.88),
    ]

    council = TriCameralCouncil(
        cell_registry=InMemoryCellRegistry(cells),
        cell_executor=SimulatedCellExecutor(),
        fusion=ReproducibleFusion(),
        zarc_journal=InMemoryZarcJournal(),
        required_chambers=2,
        confidence_threshold=0.70,
    )

    print("--- Scenario 1: all chambers deliberate ---")
    rfe1 = RequestForEvaluation(
        rfe_id="rfe-001",
        question="Should we deploy the new cell under low-trust conditions?",
        context={"risk_profile": "elevated", "deployment_zone": "edge"},
        timestamp_ns=time.time_ns(),
    )
    d1 = council.deliberate(rfe1)
    print(f"Deliberation: {d1.deliberation_id}")
    print(f"Scores: {{ {', '.join(f'{c.value}={s:.2f}' for c, s in d1.chamber_scores.items())} }}")
    print(f"Verdict: {d1.verdict.value if d1.verdict else 'NONE'}")

    print("\n--- Scenario 2: only university and investigation ---")
    rfe2 = RequestForEvaluation(
        rfe_id="rfe-002",
        question="Is this policy change constitutional?",
        context={"article": "integrity", "proposer": "guild"},
        timestamp_ns=time.time_ns(),
    )
    d2 = council.deliberate(rfe2, chamber_filter=[Chamber.UNIVERSITY, Chamber.INVESTIGATION])
    print(f"Deliberation: {d2.deliberation_id}")
    print(f"Scores: {{ {', '.join(f'{c.value}={s:.2f}' for c, s in d2.chamber_scores.items())} }}")
    print(f"Verdict: {d2.verdict.value if d2.verdict else 'NONE'}")

    print("\n--- Scenario 3: single chamber (should be inconclusive) ---")
    rfe3 = RequestForEvaluation(
        rfe_id="rfe-003",
        question="Technical feasibility only",
        context={"subject": "latency"},
        timestamp_ns=time.time_ns(),
    )
    d3 = council.deliberate(rfe3, chamber_filter=[Chamber.GUILD])
    print(f"Deliberation: {d3.deliberation_id}")
    print(f"Scores: {{ {', '.join(f'{c.value}={s:.2f}' for c, s in d3.chamber_scores.items())} }}")
    print(f"Verdict: {d3.verdict.value if d3.verdict else 'NONE'}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo()
