"""Intelligence & Counter-Intelligence domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel, ConfigDict, Field


class IntelStatus(str, Enum):
    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    UNCERTAIN = "UNCERTAIN"
    REJECTED = "REJECTED"


class SourceDescriptor(BaseModel):
    """A source of information used by intelligence collection."""
    model_config = ConfigDict(frozen=True)

    source_id: str
    type: str  # e.g., 'email', 'document', 'external_reference'
    grade: str  # A, B, C, U, X
    date: Optional[datetime] = None
    provenance: dict[str, str] = Field(default_factory=dict)


class InformationRequirement(BaseModel):
    """What we need to know."""
    model_config = ConfigDict(frozen=True)

    id: str
    description: str
    priority: int = 1  # 1=critical, 2=high, 3=medium
    status: str = "open"


class IntelFinding(BaseModel):
    """A structured intelligence finding."""
    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    confidence: str = "UNCERTAIN"
    source_ids: tuple[str, ...] = ()
    temporal_weight: str = "MODERATE"  # RECENT, MODERATE, STALE
    status: IntelStatus = IntelStatus.DRAFT


class IntelligenceReport(BaseModel):
    """Aggregated intelligence report."""
    model_config = ConfigDict(frozen=True)

    matter_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    requirements: tuple[InformationRequirement, ...] = ()
    sources: tuple[SourceDescriptor, ...] = ()
    findings: tuple[IntelFinding, ...] = ()
    summary: str = ""


class ManipulationIndicator(BaseModel):
    """Pattern of possible manipulation or deception."""
    model_config = ConfigDict(frozen=True)

    id: str
    type: str  # e.g., 'metadata_scrubbed', 'circular_referral', 'missing_document'
    evidence_ids: tuple[str, ...] = ()
    confidence: str = "UNCERTAIN"
    note: str = ""


class DeceptionHypothesis(BaseModel):
    """A testable hypothesis about possible adversarial behavior."""
    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    based_on_claim_ids: tuple[str, ...] = ()
    confirming_evidence: str = ""
    refuting_evidence: str = ""
    confidence: str = "UNCERTAIN"


class CountermeasureRecommendation(BaseModel):
    """Defensive action for the human operator."""
    model_config = ConfigDict(frozen=True)

    id: str
    action: str
    priority: int = 1
    related_hypotheses: tuple[str, ...] = ()
    note: str = ""
