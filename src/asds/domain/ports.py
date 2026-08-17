"""Port contracts between ASDS core and ANCHORUM modules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from asds.domain.models import Evidence, OutputVersion


class IAnchorumIngestionPort(ABC):
    """What ANCHORUM needs from ASDS to do its job.

    ANCHORUM never touches the filesystem directly; it reads evidence bytes and
    writes analysis outputs back through this port. ASDS owns canonical truth.
    """

    @abstractmethod
    def create_evidence(
        self,
        workspace_id: str,
        content_ref: str,
        case_id: str | None = None,
        content: bytes | None = None,
    ) -> Evidence:
        """Register content as ASDS Evidence and return the record."""

    @abstractmethod
    def get_evidence_content(self, evidence_id: str) -> bytes:
        """ASDS resolves contentRef → bytes."""

    @abstractmethod
    def get_case_evidence(self, case_id: str) -> list[Evidence]:
        """Return all Evidence registered for a case."""

    @abstractmethod
    def register_analysis_output(
        self,
        evidence_id: str,
        analysis_type: str,
        result_json: dict[str, Any],
        content_hash: str,
    ) -> OutputVersion:
        """ANCHORUM writes its findings back as an ASDS OutputVersion."""

    @abstractmethod
    def emit_bus_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """ANCHORUM reports to Plane 1 through Bus; never writes canonical."""
