"""Epistemic domain graph for legal reasoning outputs.

Hardened with Pydantic immutability, secure content hashing, and
invariant enforcement.
"""

from __future__ import annotations

from enum import Enum
from typing import FrozenSet, Tuple, Dict, Optional, Protocol, Any
from uuid import uuid4
import hashlib

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


class EpistemicStatus(str, Enum):
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    HYPOTHESIS = "HYPOTHESIS"
    ASSUMED = "ASSUMED"
    ASSERTED = "ASSERTED"
    CONTRADICTED = "CONTRADICTED"
    UNRESOLVED = "UNRESOLVED"
    UNCERTAIN = "UNCERTAIN"
    UNSUPPORTED = "UNSUPPORTED"
    @classmethod
    def from_legal_fact_type(cls, source_type: str) -> "EpistemicStatus":
        mapping = {
            "fact": cls.OBSERVED,
            "evidence_interpretation": cls.INFERRED,
            "hypothesis": cls.HYPOTHESIS,
        }
        return mapping.get(source_type, cls.UNCERTAIN)

    @classmethod
    def from_anchorum_tag(cls, tag: str) -> "EpistemicStatus":
        mapping = {
            "FACT": cls.OBSERVED,
            "DERIVED": cls.INFERRED,
            "MODEL": cls.ASSUMED,
            "UNCERTAIN": cls.UNCERTAIN,
        }
        return mapping.get(tag, cls.UNCERTAIN)


class SecureContentStore(Protocol):
    def store(self, content: str) -> str:
        """Store content securely and return opaque reference."""
        ...

    def retrieve(self, ref: str) -> str:
        """Retrieve content by reference, with access control."""
        ...


class InMemoryContentStore(SecureContentStore):
    """Simple in-memory store for testing and development."""
    def __init__(self):
        self._store: Dict[str, str] = {}

    def store(self, content: str) -> str:
        ref = uuid4().hex
        self._store[ref] = content
        return ref

    def retrieve(self, ref: str) -> str:
        return self._store[ref]


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    content_hash: str
    content_ref: str
    source_id: str
    confidence_weight: float = Field(ge=0.0, le=1.0)
    epistemic_status: EpistemicStatus
    provenance: Dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_legal_fact(cls, fact: Any, content_store: SecureContentStore) -> "Evidence":
        """Create Evidence from a LegalFact, hashing content and storing it."""
        content = fact.content
        content_ref = content_store.store(content)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return cls(
            id=fact.fact_id,
            content_hash=content_hash,
            content_ref=content_ref,
            source_id=fact.source_id,
            confidence_weight=fact.confidence_weight,
            epistemic_status=EpistemicStatus.from_legal_fact_type(fact.source_statement_type),
            provenance={"legal_fact_id": fact.fact_id},
        )


class Proposition(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    status: EpistemicStatus
    supporting_evidence_ids: FrozenSet[str] = frozenset()
    contradicting_evidence_ids: FrozenSet[str] = frozenset()
    inference_chain: Tuple[str, ...] = ()
    provenance: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_not_orphan(self):
        if not self.supporting_evidence_ids and not self.contradicting_evidence_ids and not self.inference_chain:
            raise ValueError("Proposition must have at least one evidence reference or inference chain")
        return self

    @classmethod
    def from_rule_match(cls, rule_match: Any) -> "Proposition":
        return cls(
            id=f"prop-rule-{rule_match.rule_id}",
            text=rule_match.rule_text,
            status=EpistemicStatus.ASSERTED,
            supporting_evidence_ids=frozenset(rule_match.matched_fact_ids),
            contradicting_evidence_ids=frozenset(),
            inference_chain=(),
            provenance={"rule_id": rule_match.rule_id, "jurisdiction": rule_match.jurisdiction},
        )

    @classmethod
    def from_inference_node(cls, node: Any) -> "Proposition":
        return cls(
            id=f"prop-inf-{node.node_id}",
            text=node.conclusion,
            status=EpistemicStatus.INFERRED,
            supporting_evidence_ids=frozenset(node.premise_fact_ids),
            contradicting_evidence_ids=frozenset(),
            inference_chain=tuple(node.premise_rule_ids),
            provenance={"node_id": node.node_id, "uncertainty_reason": node.uncertainty_reason},
        )


class Contradiction(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    proposition_1_id: str
    proposition_2_id: str
    resolution_status: EpistemicStatus
    winning_evidence_id: Optional[str] = None
    losing_evidence_ids: Tuple[str, ...] = ()
    rationale: str = ""

    @classmethod
    def from_conflict_resolution(cls, conflict: Any) -> "Contradiction":
        status = EpistemicStatus.CONTRADICTED if conflict.resolved else EpistemicStatus.UNRESOLVED
        return cls(
            id=conflict.conflict_id,
            proposition_1_id="",  # To be linked later
            proposition_2_id="",  # To be linked later
            resolution_status=status,
            winning_evidence_id=conflict.winning_stream_id,
            losing_evidence_ids=tuple(conflict.loser_stream_ids),
            rationale=conflict.rationale,
        )


class EpistemicGraph(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    evidences: Tuple[Evidence, ...] = ()
    propositions: Tuple[Proposition, ...] = ()
    contradictions: Tuple[Contradiction, ...] = ()
    provenance_metadata: Dict[str, str] = Field(default_factory=dict)

    def get_orphaned_propositions(self) -> Tuple[Proposition, ...]:
        return tuple(
            p for p in self.propositions
            if not p.supporting_evidence_ids and not p.contradicting_evidence_ids and not p.inference_chain
        )

    def get_unresolved_contradictions(self) -> Tuple[Contradiction, ...]:
        return tuple(c for c in self.contradictions if c.resolution_status == EpistemicStatus.UNRESOLVED)

    def validate_referential_integrity(self) -> list[str]:
        """Return list of violations (empty if all references resolve)."""
        evidence_ids = {e.id for e in self.evidences}
        proposition_ids = {p.id for p in self.propositions}
        violations = []

        for p in self.propositions:
            for ev_id in p.supporting_evidence_ids | p.contradicting_evidence_ids:
                if ev_id not in evidence_ids:
                    violations.append(f"Proposition {p.id} references missing evidence {ev_id}")
            for pred_id in p.inference_chain:
                if pred_id not in proposition_ids:
                    violations.append(f"Proposition {p.id} has missing inference predecessor {pred_id}")

        for c in self.contradictions:
            if c.proposition_1_id and c.proposition_1_id not in proposition_ids:
                violations.append(f"Contradiction {c.id} references missing proposition {c.proposition_1_id}")
            if c.proposition_2_id and c.proposition_2_id not in proposition_ids:
                violations.append(f"Contradiction {c.id} references missing proposition {c.proposition_2_id}")
            if c.winning_evidence_id and c.winning_evidence_id not in evidence_ids:
                violations.append(f"Contradiction {c.id} references missing winning evidence {c.winning_evidence_id}")
            for ev_id in c.losing_evidence_ids:
                if ev_id not in evidence_ids:
                    violations.append(f"Contradiction {c.id} references missing losing evidence {ev_id}")

        return violations


    def link_contradictions(self) -> "EpistemicGraph":
        """Return a new graph where contradictions have proposition IDs filled.

        Heuristic: For each contradiction, find propositions that reference
        the winning and losing evidence IDs in their supporting or contradicting
        sets. Assign the first matching proposition IDs.
        """
        new_contradictions = []
        for c in self.contradictions:
            p1_id = c.proposition_1_id
            p2_id = c.proposition_2_id

            if not p1_id and c.winning_evidence_id:
                for p in self.propositions:
                    if c.winning_evidence_id in p.supporting_evidence_ids:
                        p1_id = p.id
                        break
            if not p2_id and c.losing_evidence_ids:
                for ev_id in c.losing_evidence_ids:
                    for p in self.propositions:
                        if ev_id in p.supporting_evidence_ids or ev_id in p.contradicting_evidence_ids:
                            p2_id = p.id
                            break
                    if p2_id:
                        break

            new_contradictions.append(
                Contradiction(
                    id=c.id,
                    proposition_1_id=p1_id or "",
                    proposition_2_id=p2_id or "",
                    resolution_status=c.resolution_status,
                    winning_evidence_id=c.winning_evidence_id,
                    losing_evidence_ids=c.losing_evidence_ids,
                    rationale=c.rationale,
                )
            )

        return EpistemicGraph(
            case_id=self.case_id,
            evidences=self.evidences,
            propositions=self.propositions,
            contradictions=tuple(new_contradictions),
            provenance_metadata=self.provenance_metadata,
        )
