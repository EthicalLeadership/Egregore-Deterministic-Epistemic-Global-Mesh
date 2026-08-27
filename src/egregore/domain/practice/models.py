"""Practice standard domain models.

A PracticeStandard is the immutable production profile derived from a
Matter's Scope. It tells the document compiler exactly which sections,
citation rules, tone, and formatting are required.

This module contains no logic beyond validation – it is pure domain.
"""

from __future__ import annotations

from enum import Enum
from typing import FrozenSet, Tuple, Optional, Any

from pydantic import BaseModel, ConfigDict, Field


class Jurisdiction(str, Enum):
    QUEBEC = "QC"
    ONTARIO = "ON"
    FEDERAL_CANADA = "CA"

    @classmethod
    def from_label(cls, label: str) -> "Jurisdiction":
        mapping = {
            "quebec": cls.QUEBEC,
            "qc": cls.QUEBEC,
            "ontario": cls.ONTARIO,
            "on": cls.ONTARIO,
            "canada": cls.FEDERAL_CANADA,
            "federal": cls.FEDERAL_CANADA,
        }
        key = label.strip().lower()
        if key not in mapping:
            raise ValueError(f"Unknown jurisdiction label: {label}")
        return mapping[key]


class Forum(str, Enum):
    SUPERIOR_COURT = "superior_court"
    COURT_OF_APPEAL = "court_of_appeal"
    SUPREME_COURT = "supreme_court"
    TRIBUNAL = "tribunal"
    ARBITRATION = "arbitration"
    EXECUTIVE_BOARD = "executive_board"
    DIPLOMATIC_CHANNEL = "diplomatic_channel"
    INTERNAL = "internal"

    @classmethod
    def from_label(cls, label: str) -> "Forum":
        mapping = {
            "superior court": cls.SUPERIOR_COURT,
            "court of appeal": cls.COURT_OF_APPEAL,
            "supreme court": cls.SUPREME_COURT,
            "tribunal": cls.TRIBUNAL,
            "arbitration": cls.ARBITRATION,
            "board": cls.EXECUTIVE_BOARD,
            "diplomatic": cls.DIPLOMATIC_CHANNEL,
            "internal": cls.INTERNAL,
        }
        key = label.strip().lower()
        if key not in mapping:
            raise ValueError(f"Unknown forum label: {label}")
        return mapping[key]


class DocumentType(str, Enum):
    MOTION = "motion"
    MEMORANDUM = "memorandum"
    BRIEF = "brief"
    SETTLEMENT_LETTER = "settlement_letter"
    STRATEGY_MEMO = "strategy_memo"
    NEGOTIATION_POSITION = "negotiation_position"
    DIPLOMATIC_BRIEFING = "diplomatic_briefing"
    CONTRACT_CLAUSE = "contract_clause"
    OPINION = "opinion"

    @classmethod
    def from_label(cls, label: str) -> "DocumentType":
        mapping = {
            "motion": cls.MOTION,
            "memorandum": cls.MEMORANDUM,
            "brief": cls.BRIEF,
            "settlement letter": cls.SETTLEMENT_LETTER,
            "strategy memo": cls.STRATEGY_MEMO,
            "negotiation position": cls.NEGOTIATION_POSITION,
            "diplomatic briefing": cls.DIPLOMATIC_BRIEFING,
            "contract clause": cls.CONTRACT_CLAUSE,
            "opinion": cls.OPINION,
        }
        key = label.strip().lower()
        if key not in mapping:
            raise ValueError(f"Unknown document type label: {label}")
        return mapping[key]


class Audience(str, Enum):
    COURT = "court"
    OPPOSING_COUNSEL = "opposing_counsel"
    CLIENT = "client"
    EXECUTIVE = "executive"
    DIPLOMATIC = "diplomatic"
    INTERNAL = "internal"
    PUBLIC = "public"

    @classmethod
    def from_label(cls, label: str) -> "Audience":
        mapping = {
            "court": cls.COURT,
            "judge": cls.COURT,
            "opposing counsel": cls.OPPOSING_COUNSEL,
            "client": cls.CLIENT,
            "executive": cls.EXECUTIVE,
            "diplomatic": cls.DIPLOMATIC,
            "internal": cls.INTERNAL,
            "public": cls.PUBLIC,
        }
        key = label.strip().lower()
        if key not in mapping:
            raise ValueError(f"Unknown audience label: {label}")
        return mapping[key]


class Scope(BaseModel):
    """Input scope that determines the production standard."""

    model_config = ConfigDict(frozen=True)

    jurisdiction: Jurisdiction
    forum: Forum
    document_type: DocumentType
    audience: Audience
    purpose: str
    professional_context: str = Field(default="legal")
    procedural_posture: Optional[str] = None


class PracticeStandard(BaseModel):
    """Immutable production standard resolved from a Scope."""

    model_config = ConfigDict(frozen=True)

    id: str
    jurisdiction: Jurisdiction
    forum: Forum
    document_type: DocumentType
    audience: Audience

    required_sections: Tuple[str, ...]
    optional_sections: Tuple[str, ...] = ()
    prohibited_content: Tuple[str, ...] = ()

    citation_standard: str = "McGill Guide"
    authority_standard: str = "primary"
    tone: str = "formal"
    formality: str = "high"
    advocacy_level: str = "controlled"

    formatting: FrozenSet[str] = frozenset()
    pagination: bool = True
    exhibits: bool = False
    attachments: bool = False

    metadata: Optional[Any] = Field(default=None)
