from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from egregore.domain.semantics.canonical_event_envelope import (
    DEFAULT_PRODUCER_IDENTITY,
    build_canonical_event_envelope,
    canonical_event_envelope_payload,
)
from egregore.domain.semantics.domain_adapters import DossierSemanticsDomainAdapter
from egregore.domain.semantics.ports import ISemanticsDomainAdapter
from egregore.domain.semantics_models import (
    AuditEvent,
    DossierSnapshot,
    GenerateDossierCommand,
    OutboxEntry,
)
from egregore.shared.stable_ids import stable_event_id_from_envelope_components


@dataclass(frozen=True)
class GenerateArtifacts:
    snapshot: DossierSnapshot
    events: tuple[AuditEvent, ...]
    outbox_entries: tuple[OutboxEntry, ...]


def derive_generate_artifacts(
    *,
    command: GenerateDossierCommand,
    timestamp_ns: int,
    version_id: str,
    version_number: int,
    engine_data: Mapping[str, Any],
    engine_metadata: Mapping[str, Any],
    event_schema_version: str,
    event_seqs: Sequence[int] = (0, 1),
    domain_adapter: ISemanticsDomainAdapter | None = None,
) -> GenerateArtifacts:
    """
    Deterministic derivation of snapshot + audit events + outbox for `generate dossier`.

    NON-NEGOTIABLE closure invariant:
    - Orchestration code MUST NOT construct AuditEvent/OutboxEntry directly.
    - This function is the single source of truth for artifacts.

    Identity derivation compatibility:
    - AuditEvent.event_id uses stable_event_id_from_components(event_type, event_index).
    - We set event_index == event_seq to match current executor semantics.
    - OutboxEntry.outbox_id uses stable_event_id_from_components(event_type="OUTBOX", event_index=0).
    """
    if len(event_seqs) != 2:
        raise ValueError("event_seqs must be (requested_seq, generated_seq)")

    ev_seq_req, ev_seq_gen = int(event_seqs[0]), int(event_seqs[1])
    task_contract = command.to_task_contract()
    adapter = domain_adapter or DossierSemanticsDomainAdapter()

    requested_event_type = adapter.requested_event_type()
    generated_event_type = adapter.generated_event_type()

    envelope = build_canonical_event_envelope(
        command=command,
        timestamp_ns=timestamp_ns,
        producer_identity=DEFAULT_PRODUCER_IDENTITY,
    )
    envelope_payload = canonical_event_envelope_payload(envelope)

    events = (
        AuditEvent(
            organization_id=command.organization_id,
            case_id=command.case_id,
            version_id=version_id,
            event_type=requested_event_type,
            event_id=stable_event_id_from_envelope_components(
                organization_id=command.organization_id,
                case_id=command.case_id,
                version_id=version_id,
                event_type=requested_event_type,
                event_index=ev_seq_req,
                timestamp_ns=timestamp_ns,
                causality_id=command.causality_id,
                event_schema_version=event_schema_version,
                event_seq=ev_seq_req,
                envelope_id=envelope.envelope_id,
                correlation_id=envelope.correlation_id,
                producer_identity=envelope.producer_identity,
                envelope_schema_version=envelope.envelope_schema_version,
            ),
            timestamp_ns=timestamp_ns,
            event_schema_version=event_schema_version,
            event_seq=ev_seq_req,
            causality_id=command.causality_id,
            payload={
                "engine_version": command.engine_version,
                "policy_version": command.policy_version,
                "input_fingerprint": command.input_fingerprint,
                **envelope_payload,
                "task_contract": {
                    "task_id": task_contract.task_id,
                    "intent": task_contract.intent,
                    "constraints": list(task_contract.constraints),
                    "inputs": dict(task_contract.inputs),
                    "allowed_tools": list(task_contract.allowed_tools),
                    "policy_level": task_contract.policy_level,
                    "expected_outputs": list(task_contract.expected_outputs),
                    "replayable": task_contract.replayable,
                },
            },
        ),
        AuditEvent(
            organization_id=command.organization_id,
            case_id=command.case_id,
            version_id=version_id,
            event_type=generated_event_type,
            event_id=stable_event_id_from_envelope_components(
                organization_id=command.organization_id,
                case_id=command.case_id,
                version_id=version_id,
                event_type=generated_event_type,
                event_index=ev_seq_gen,
                timestamp_ns=timestamp_ns,
                causality_id=command.causality_id,
                event_schema_version=event_schema_version,
                event_seq=ev_seq_gen,
                envelope_id=envelope.envelope_id,
                correlation_id=envelope.correlation_id,
                producer_identity=envelope.producer_identity,
                envelope_schema_version=envelope.envelope_schema_version,
            ),
            timestamp_ns=timestamp_ns,
            event_schema_version=event_schema_version,
            event_seq=ev_seq_gen,
            causality_id=command.causality_id,
            payload={
                "engine_version": command.engine_version,
                "policy_version": command.policy_version,
                "metadata": dict(engine_metadata),
                **envelope_payload,
            },
        ),
    )

    outbox_entries = (
        OutboxEntry(
            organization_id=command.organization_id,
            case_id=command.case_id,
            version_id=version_id,
            causality_id=command.causality_id,
            side_effect_type=adapter.outbox_side_effect_type(),
            outbox_id=stable_event_id_from_envelope_components(
                organization_id=command.organization_id,
                case_id=command.case_id,
                version_id=version_id,
                event_type="OUTBOX",
                event_index=0,
                timestamp_ns=timestamp_ns,
                causality_id=command.causality_id,
                event_schema_version=event_schema_version,
                event_seq=0,
                envelope_id=envelope.envelope_id,
                correlation_id=envelope.correlation_id,
                producer_identity=envelope.producer_identity,
                envelope_schema_version=envelope.envelope_schema_version,
            ),
            payload={
                **dict(
                    adapter.outbox_payload(
                        engine_data=engine_data,
                        generated_event_type=generated_event_type,
                    )
                ),
                **envelope_payload,
            },
        ),
    )

    snapshot = DossierSnapshot(
        organization_id=command.organization_id,
        case_id=command.case_id,
        version_number=version_number,
        version_id=version_id,
        data=dict(engine_data),
    )

    return GenerateArtifacts(
        snapshot=snapshot, events=events, outbox_entries=outbox_entries
    )


def _as_dict_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    return None


def journal_deserialize_audit_events(
    *, serialized_events: Sequence[Any]
) -> tuple[AuditEvent, ...]:
    """
    Reconstruct AuditEvent objects from journal-serialized dicts.

    IMPORTANT: AuditEvent(...) constructor calls must remain single-sourced
    inside this module (enforced by tests).
    """
    events: list[AuditEvent] = []
    for raw in serialized_events:
        obj = _as_dict_mapping(raw)
        if obj is None:
            continue
        events.append(AuditEvent(**dict(obj)))
    return tuple(events)


def journal_deserialize_outbox_entries(
    *, serialized_outboxes: Sequence[Any]
) -> tuple[OutboxEntry, ...]:
    """
    Reconstruct OutboxEntry objects from journal-serialized dicts.

    IMPORTANT: OutboxEntry(...) constructor calls must remain single-sourced
    inside this module (enforced by tests).
    """
    outbox_entries: list[OutboxEntry] = []
    for raw in serialized_outboxes:
        obj = _as_dict_mapping(raw)
        if obj is None:
            continue
        outbox_entries.append(OutboxEntry(**dict(obj)))
    return tuple(outbox_entries)
