# epistemic marker: provenance / auditability
from __future__ import annotations

import hashlib

from egregore.domain.semantics_models import GenerateDossierCommand


def _sha256_hex_from_updates(*, updates: tuple[bytes, ...]) -> str:
    """
    Allocation-reduction helper for deterministic IDs.

    We avoid building the full raw f-string and encoding it once.
    Instead, we stream component bytes into hashlib and return hexdigest.

    Output is still the standard lowercase SHA256 hex digest.
    """
    h = hashlib.sha256()
    for chunk in updates:
        h.update(chunk)
    return h.hexdigest()


def stable_event_id(
    *,
    command: GenerateDossierCommand,
    version_id: str,
    event_type: str,
    event_index: int,
    timestamp_ns: int,
    event_schema_version: str,
    event_seq: int,
) -> str:
    """
    Deterministic event_id derivation for replay-stable audit events.

    Semantic closure v0:
    - must incorporate event_schema_version
    - must incorporate logical ordering event_seq
    """
    return stable_event_id_from_components(
        organization_id=command.organization_id,
        case_id=command.case_id,
        version_id=version_id,
        event_type=event_type,
        event_index=event_index,
        timestamp_ns=timestamp_ns,
        causality_id=command.causality_id,
        event_schema_version=event_schema_version,
        event_seq=event_seq,
    )


def stable_event_id_from_components(
    *,
    organization_id: str,
    case_id: str,
    version_id: str,
    event_type: str,
    event_index: int,
    timestamp_ns: int,
    causality_id: str,
    event_schema_version: str,
    event_seq: int,
) -> str:
    """
    Same stable identity derivation as `stable_event_id`, but avoids requiring
    the full GenerateDossierCommand object (useful for replay validation).
    """
    sep = b"|"
    updates = (
        organization_id.encode("utf-8"),
        sep,
        case_id.encode("utf-8"),
        sep,
        version_id.encode("utf-8"),
        sep,
        event_type.encode("utf-8"),
        sep,
        str(event_index).encode("utf-8"),
        sep,
        str(timestamp_ns).encode("utf-8"),
        sep,
        causality_id.encode("utf-8"),
        sep,
        event_schema_version.encode("utf-8"),
        sep,
        str(event_seq).encode("utf-8"),
    )
    return _sha256_hex_from_updates(updates=updates)


def stable_event_id_from_envelope_components(
    *,
    organization_id: str,
    case_id: str,
    version_id: str,
    event_type: str,
    event_index: int,
    timestamp_ns: int,
    causality_id: str,
    event_schema_version: str,
    event_seq: int,
    # Canonical envelope fields
    envelope_id: str,
    correlation_id: str,
    producer_identity: str,
    envelope_schema_version: str,
) -> str:
    """
    Replay-stable identity derivation that additionally binds canonical
    event envelope metadata.

    This is a strict refinement: the same deterministic command lineage
    should produce the same IDs in live + replay.
    """
    sep = b"|"
    updates = (
        organization_id.encode("utf-8"),
        sep,
        case_id.encode("utf-8"),
        sep,
        version_id.encode("utf-8"),
        sep,
        event_type.encode("utf-8"),
        sep,
        str(event_index).encode("utf-8"),
        sep,
        str(timestamp_ns).encode("utf-8"),
        sep,
        causality_id.encode("utf-8"),
        sep,
        event_schema_version.encode("utf-8"),
        sep,
        str(event_seq).encode("utf-8"),
        sep,
        b"env:",
        envelope_id.encode("utf-8"),
        sep,
        b"corr:",
        correlation_id.encode("utf-8"),
        sep,
        b"prod:",
        producer_identity.encode("utf-8"),
        sep,
        b"env_schema:",
        envelope_schema_version.encode("utf-8"),
    )
    return _sha256_hex_from_updates(updates=updates)
