"""
Constitutional escalation logging and freeze integration.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Protocol

from egregore.domain.federation_constitution import Escalation, EscalationLevel
from egregore.domain.provenance_model import ProvenanceEvent
from egregore.interface.provenance_port import IProvenanceSink


class IEscalationNotifier(Protocol):
    def notify(self, escalation: Escalation) -> None: ...


class EscalationService:
    """Create escalations, log them to .zarc, and optionally freeze the runtime."""

    def __init__(
        self,
        provenance_sink: IProvenanceSink | None = None,
        freeze_controller: Any = None,
        notifier: IEscalationNotifier | None = None,
    ) -> None:
        self._provenance = provenance_sink
        self._freeze = freeze_controller
        self._notifier = notifier

    def open(
        self,
        level: EscalationLevel,
        trigger: str,
        affected_nodes: list[str],
        evidence_hashes: list[str] | None = None,
    ) -> Escalation:
        escalation = Escalation(
            escalation_id=f"esc-{uuid.uuid4().hex[:16]}",
            level=level,
            trigger=trigger,
            affected_nodes=tuple(affected_nodes),
            evidence_hashes=tuple(evidence_hashes or []),
            timestamp_ns=time.time_ns(),
        )
        self._emit(
            "escalation_opened",
            {
                "escalation_id": escalation.escalation_id,
                "level": escalation.level.value,
                "trigger": escalation.trigger,
                "affected_nodes": list(escalation.affected_nodes),
                "evidence_hashes": list(escalation.evidence_hashes),
            },
        )
        if self._notifier is not None:
            self._notifier.notify(escalation)
        if (
            escalation.level in (EscalationLevel.CRITICAL, EscalationLevel.OVERRIDE)
            and self._freeze is not None
        ):
            self._freeze.freeze(reason=trigger, evidence=escalation.escalation_id)
        return escalation

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._provenance is None:
            return
        self._provenance.append(
            ProvenanceEvent(
                engine="federation",
                event=event,
                payload=payload,
                ts_ns=time.time_ns(),
            )
        )
