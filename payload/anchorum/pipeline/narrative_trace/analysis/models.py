"""
ANCHORUM Pipeline — Typed Data Contracts
File: anchorum/pipeline/narrative_trace/analysis/models.py

Every record is immutable (frozen dataclass). Every field has a docstring.
Every enum value is auditable in tribunal proceedings.
"""

from __future__ import annotations
import datetime
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional, List, Dict, Any


class EpistemicTag(str, Enum):
    """Grounding of every claim in the demolition timeline."""
    FACT = "FACT"           # Directly observable from primary source
    DERIVED = "DERIVED"     # Logical inference from multiple FACTs
    MODEL = "MODEL"         # Pattern match against known adversarial tactics
    UNCERTAIN = "UNCERTAIN" # Insufficient evidence to determine


class BurdenShiftTag(str, Enum):
    """Did the counterparty improperly shift procedural or evidentiary burden?"""
    YES = "YES"
    NO = "NO"
    IMPLICIT = "IMPLICIT"   # Burden shift via rhetorical framing, not explicit demand


class Severity(str, Enum):
    """Consequence severity for tribunal readiness prioritization."""
    CRITICAL = "CRITICAL"     # Undermines entire counterparty narrative
    HIGH = "HIGH"             # Significant contradiction, affects credibility
    MEDIUM = "MEDIUM"         # Minor inconsistency, context-dependent
    LOW = "LOW"               # Cosmetic or immaterial discrepancy


@dataclass(frozen=True)
class SourceProvenance:
    """Immutable fingerprint of where this record came from."""
    file_path: Path
    file_hash_sha256: str
    ingestion_timestamp: datetime.datetime
    parser_version: str
    raw_bytes_size: int


@dataclass(frozen=True)
class CommunicationRecord:
    """
    A single counterparty communication (email, letter, meeting note, phone log).
    Immutable. Every claim extracted from this record is tagged with its source.
    """
    record_id: str                          # Stable UUIDv4
    provenance: SourceProvenance
    communication_type: str                   # "email" | "letter" | "meeting_note" | "phone_log" | "form"
    date_sent: Optional[datetime.date]        # From header / postmark / metadata
    date_received: Optional[datetime.date]    # When user obtained it
    sender: str                              # Counterparty entity name
    recipient: str                            # User or user's representative
    subject: Optional[str]                   # Normalized subject line
    body_text: str                           # Stripped plain text, HTML tags removed
    body_hash: str                           # SHA256 of normalized body for dedup
    claims_extracted: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Runtime invariant: every record must have at least one date or be flagged UNCERTAIN
        if self.date_sent is None and self.date_received is None:
            object.__setattr__(self, "metadata", {
                **self.metadata,
                "date_uncertain": True,
                "epistemic_tag": EpistemicTag.UNCERTAIN,
            })


@dataclass(frozen=True)
class EvidenceRecord:
    """
    A documentary evidence item (medical record, insurer correspondence, employer file).
    Immutable. Cross-referenced against CommunicationRecord claims.
    """
    record_id: str
    provenance: SourceProvenance
    evidence_type: str                        # "medical_record" | "insurer_correspondence" | "employer_file" | "legal_statute" | "expert_opinion"
    document_date: Optional[datetime.date]
    creation_date: Optional[datetime.date]     # File system / metadata creation
    modification_date: Optional[datetime.date] # File system / metadata modification
    source_entity: str                        # Who produced this document
    content_text: str
    content_hash: str
    key_facts: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimelineEntry:
    """
    Single row in the demolition timeline. Every field is tribunal-ready.
    """
    entry_id: str                             # Sequential within timeline
    date: Optional[datetime.date]             # The date being analyzed
    counterparty_claim: str                  # Exact quote or paraphrase with source ref
    documentary_evidence: str                # Exact evidence refutation or support
    verdict: EpistemicTag                    # FACT | DERIVED | MODEL | UNCERTAIN
    burden_shift: BurdenShiftTag             # YES | NO | IMPLICIT
    severity: Severity                       # CRITICAL | HIGH | MEDIUM | LOW
    communication_source_id: str             # record_id of CommunicationRecord
    evidence_source_ids: List[str]           # record_ids of EvidenceRecords
    demolition_note: str                    # Why this entry matters
    raw_quote_communication: Optional[str] = None
    raw_quote_evidence: Optional[str] = None


@dataclass(frozen=True)
class DemolitionTimeline:
    """
    Complete auditable output of the narrative trace.
    """
    case_id: str
    generated_at: datetime.datetime
    tracer_version: str
    entries: List[TimelineEntry] = field(default_factory=list)
    unverified_claims: List[str] = field(default_factory=list)
    evidence_gaps: List[str] = field(default_factory=list)
    summary: str = ""

    def to_markdown(self) -> str:
        """Generate tribunal-ready markdown report."""
        lines = [
            f"# Demolition Timeline: {self.case_id}",
            f"",
            f"**Generated:** {self.generated_at.isoformat()}  ",
            f"**Tracer Version:** {self.tracer_version}  ",
            f"**Epistemic Grounding:** Every claim tagged [FACT | DERIVED | MODEL | UNCERTAIN]  ",
            f"",
            f"---",
            f"",
            f"## Entries",
            f"",
            f"| Date | Counterparty Claim | Documentary Evidence | Verdict | Burden Shift | Severity |",
            f"|------|-------------------|---------------------|---------|-------------|----------|",
        ]
        for entry in self.entries:
            date_str = entry.date.isoformat() if entry.date else "DATE_UNCERTAIN"
            lines.append(
                f"| {date_str} | {entry.counterparty_claim[:80]}... | "
                f"{entry.documentary_evidence[:80]}... | {entry.verdict.value} | "
                f"{entry.burden_shift.value} | {entry.severity.value} |"
            )
        lines.extend([
            f"",
            f"---",
            f"",
            f"## Unverified Claims (No Documentary Match)",
            f"",
        ])
        for claim in self.unverified_claims:
            lines.append(f"- {claim}")
        lines.extend([
            f"",
            f"## Evidence Gaps (Documentary Evidence Without Counterparty Claim)",
            f"",
        ])
        for gap in self.evidence_gaps:
            lines.append(f"- {gap}")
        lines.extend([
            f"",
            f"## Summary",
            f"",
            self.summary,
            f"",
            f"---",
            f"*This report was generated by ANCHORUM Narrative Trace Pipeline. "
            f"All claims are verifiable against primary source documents. "
            f"UNCERTAIN tags indicate insufficient evidence, not falsity.*",
        ])
        return "\\n".join(lines)

