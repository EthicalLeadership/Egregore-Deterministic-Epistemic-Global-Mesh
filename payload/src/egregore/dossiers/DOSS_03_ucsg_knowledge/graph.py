"""DOSS-03: UCSG Knowledge Graph — Semantic backbone and constrained reasoning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class StatementType(StrEnum):
    FACT = "fact"
    CLASSIFICATION = "classification"
    EVIDENCE_INTERPRETATION = "evidence_interpretation"
    HYPOTHESIS = "hypothesis"


@dataclass(frozen=True)
class FactStatement:
    statement_type: StatementType = StatementType.FACT
    content: str = ""
    source_id: str = ""


_FORBIDDEN_PHRASES: set[str] = {
    "establishes liability",
    "proves wrongdoing",
    "legal conclusion",
    "legally sufficient",
    "confirmed retaliation",
    "confirmed violation",
}


class UCSGKnowledgeGraph:
    """Unified Constrained Semantic Graph for Egregore knowledge representation.

    Supports fact storage, constraint checking, and basic graph edges between
    related facts.
    """

    def __init__(self) -> None:
        self.facts: list[FactStatement] = []
        self._edges: dict[int, set[int]] = (
            {}
        )  # fact_index -> set of related fact indices
        self._forbidden = _FORBIDDEN_PHRASES

    def add_fact(self, content: str, source_id: str) -> FactStatement:
        fact = FactStatement(
            statement_type=StatementType.FACT,
            content=content,
            source_id=source_id,
        )
        self.facts.append(fact)
        return fact

    def add_edge(self, from_fact: FactStatement, to_fact: FactStatement) -> None:
        """Create a directed edge between two facts already in the graph."""
        try:
            from_idx = self.facts.index(from_fact)
            to_idx = self.facts.index(to_fact)
        except ValueError as exc:
            raise ValueError(
                "Both facts must be added to the graph before linking"
            ) from exc
        self._edges.setdefault(from_idx, set()).add(to_idx)

    def related_facts(self, fact: FactStatement) -> list[FactStatement]:
        """Return all facts directly connected from the given fact."""
        try:
            idx = self.facts.index(fact)
        except ValueError:
            return []
        related = self._edges.get(idx, set())
        return [self.facts[i] for i in related]

    def normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    def check_constraint(self, text: str) -> tuple[bool, str | None]:
        normalized = self.normalize(text)
        for phrase in self._forbidden:
            if phrase in normalized:
                return False, f"Forbidden phrase detected: {phrase}"
        return True, None

    def query(self, keyword: str) -> list[FactStatement]:
        return [f for f in self.facts if keyword.lower() in f.content.lower()]
