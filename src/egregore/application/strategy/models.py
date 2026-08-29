"""Strategy domain models."""

from __future__ import annotations

from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict, Field


class Perspective(str, Enum):
    LEGAL = "legal"
    FINANCIAL = "financial"
    POLITICAL = "political"
    REPUTATIONAL = "reputational"
    ADVERSARIAL = "adversarial"
    LONG_TERM = "long_term"


class TimeHorizon(str, Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class StrategyScope(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: str
    jurisdiction: str = "QC"
    goals: str
    constraints: tuple[str, ...] = ()
    stakeholders: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    evidence_contents: dict[str, str] = Field(default_factory=dict)

    def to_context(self) -> dict[str, Any]:
        return self.model_dump()


class PerspectiveAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    perspective: Perspective
    horizon: TimeHorizon
    findings: list[str]
    risks: list[str]
    options: list[str]
    evidence_ids: tuple[str, ...] = ()
    provenance: dict[str, Any] = Field(default_factory=dict)


class StrategyMemo(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: str
    scope: StrategyScope
    analyses: tuple[PerspectiveAnalysis, ...]
    summary: str
    status: str = "DRAFT"
