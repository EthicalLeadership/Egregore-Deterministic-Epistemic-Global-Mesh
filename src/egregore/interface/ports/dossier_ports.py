from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from egregore.domain.semantics_models import CommandAck

DossierGenerateResult = CommandAck


@dataclass(frozen=True)
class DossierGenerateRequest:
    organization_id: str
    case_id: str
    actor_id: str

    input_fingerprint: str
    engine_version: str
    policy_version: str
    input_payload: dict[str, Any]

    causality_id: str
    request_id: str | None = None
    timestamp_ns: int | None = None

    # Transport-only routing hint used by the HTTP interface/facade.
    vertical: str | None = None


class DossierServiceFacade(Protocol):
    def generate(self, *, request: DossierGenerateRequest) -> DossierGenerateResult: ...
