from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from egregore.application.dossier_generate_service import DossierGenerateRequest


def envelope_to_dossier_request(envelope: Mapping[str, Any]) -> DossierGenerateRequest:
    """
    Deterministic structural mapping ONLY.

    RULES:
    - No branching logic
    - No enrichment
    - No default injection beyond structural passthrough
    - Must be replay-safe
    """
    intent = envelope["intent"]
    actor = envelope["actor"]
    governance = envelope["governance"]
    determinism = envelope["determinism"]

    return DossierGenerateRequest(
        organization_id=actor["organization_id"],
        actor_id=actor["actor_id"],
        case_id=envelope.get("causality_id"),
        input_payload=intent["input_payload"],
        input_fingerprint=determinism["input_fingerprint"],
        engine_version=determinism["engine_version"],
        policy_version=governance["policy_version"],
        causality_id=envelope["causality_id"],
        timestamp_ns=None,
    )
