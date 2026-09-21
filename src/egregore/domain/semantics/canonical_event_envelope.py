# epistemic marker: provenance / auditability
from __future__ import annotations

from dataclasses import dataclass

from egregore.domain.semantics_models import GenerateDossierCommand

CANONICAL_EVENT_ENVELOPE_SCHEMA_VERSION: str = "env-v1"
DEFAULT_PRODUCER_IDENTITY: str = "core_plane"


@dataclass(frozen=True)
class CanonicalEventEnvelope:
    """
    Canonical event envelope metadata.

    Deterministic inputs:
    - command (causation_id, request_id)
    - timestamp_ns
    - producer_identity (deterministic constant for a given producer)

    Embedded into AuditEvent/OutboxEntry payloads so replay can validate
    identity metadata without relying on executor-side in-place mutation.
    """

    envelope_id: str
    causation_id: str
    correlation_id: str
    logical_timestamp_ns: int
    producer_identity: str
    envelope_schema_version: str = CANONICAL_EVENT_ENVELOPE_SCHEMA_VERSION


def build_canonical_event_envelope(
    *,
    command: GenerateDossierCommand,
    timestamp_ns: int,
    producer_identity: str = DEFAULT_PRODUCER_IDENTITY,
) -> CanonicalEventEnvelope:
    # envelope_id / correlation_id are deterministic from the request lineage.
    envelope_id = command.request_id or command.causality_id
    correlation_id = command.request_id or command.causality_id

    return CanonicalEventEnvelope(
        envelope_id=envelope_id,
        causation_id=command.causality_id,
        correlation_id=correlation_id,
        logical_timestamp_ns=int(timestamp_ns),
        producer_identity=str(producer_identity),
        envelope_schema_version=CANONICAL_EVENT_ENVELOPE_SCHEMA_VERSION,
    )


def canonical_event_envelope_payload(
    envelope: CanonicalEventEnvelope,
) -> dict[str, object]:
    return {
        "envelope_id": envelope.envelope_id,
        "causation_id": envelope.causation_id,
        "correlation_id": envelope.correlation_id,
        "logical_timestamp_ns": envelope.logical_timestamp_ns,
        "producer_identity": envelope.producer_identity,
        "envelope_schema_version": envelope.envelope_schema_version,
    }
