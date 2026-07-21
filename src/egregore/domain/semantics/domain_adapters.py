from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class DossierSemanticsDomainAdapter:
    def requested_event_type(self) -> str:
        return "DOSSIER_GENERATION_REQUESTED"

    def generated_event_type(self) -> str:
        return "DOSSIER_GENERATED"

    def outbox_side_effect_type(self) -> str:
        return "GOVERNANCE_INGEST"

    def outbox_payload(
        self, *, engine_data: Mapping[str, Any], generated_event_type: str
    ) -> Mapping[str, Any]:
        return {
            "engine": "dossier_engine",
            "event": generated_event_type,
            "dossier_data": dict(engine_data),
        }
