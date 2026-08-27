"""Control models for the Assurance Engine."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ControlStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    BLOCKER = "BLOCKER"


class ControlResult(BaseModel):
    """Result of a single assurance control."""
    control_id: str
    name: str
    status: ControlStatus
    message: str
    details: Optional[Dict[str, Any]] = Field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()

    model_config = {"frozen": True}


class AssuranceReport(BaseModel):
    """Aggregate results of all controls run on a graph."""
    case_id: str
    controls: tuple[ControlResult, ...]
    timestamp: str
    overall_status: ControlStatus

    model_config = {"frozen": True}

    @property
    def blockers(self) -> tuple[ControlResult, ...]:
        return tuple(c for c in self.controls if c.status == ControlStatus.BLOCKER)

    @property
    def failures(self) -> tuple[ControlResult, ...]:
        return tuple(c for c in self.controls if c.status == ControlStatus.FAIL)

    @property
    def warnings(self) -> tuple[ControlResult, ...]:
        return tuple(c for c in self.controls if c.status == ControlStatus.WARN)
