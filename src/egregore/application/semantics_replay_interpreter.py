from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from egregore.application.cbi_0_orchestrated_executor import (
    enforce_cbi0_runtime_chain_for_legal_ir,
)
from egregore.domain.legal_agent.projection_registry import StaticProjectionRegistry
from egregore.domain.semantics.canonical_event_envelope import (
    DEFAULT_PRODUCER_IDENTITY,
    build_canonical_event_envelope,
    canonical_event_envelope_payload,
)
from egregore.domain.semantics.ir_deserialization import (
    CanonicalIRDeserializationError,
    deserialize_to_canonical_ir,
)
from egregore.domain.semantics.observability import (
    EQUIV_O,
    PI_O,
    evaluator_metadata_rename_transform,
    validate_envelope_contract,
)
from egregore.domain.semantics.projection_descriptor import (
    OverlapClassification,
    ProjectionDescriptor,
)
from egregore.domain.semantics_models import (
    AuditEvent,
    GenerateDossierCommand,
    OutboxEntry,
)
from egregore.interface.constraint_binding_ports import (
    ProjectionBindingError,
    RegistryValidationError,
)
from egregore.shared.stable_ids import stable_event_id_from_envelope_components


@dataclass(frozen=True)
class ReplayEquivalenceResult:
    ok: bool
    failures: Sequence[str]
    governance_trace: Sequence[str] = ()


class CorePlaneReplayInterpreter:
    """
    Reference replay interpreter (Core Plane correctness anchor).

    Contract:
    - Given the original deterministic command and the persisted artifacts
      (snapshot payload + audit events + outbox entries), re-derive the
      canonical outcomes and validate strict equivalence.

    This interpreter is pure: it performs no I/O and no wall-clock access.
    """

    def __init__(
        self,
        *,
        compute_engine_policy: Callable[[GenerateDossierCommand], Any],
        projection_descriptors: (
            Mapping[tuple[str, str], ProjectionDescriptor] | None
        ) = None,
        overlap_classifications: list[OverlapClassification] | None = None,
    ) -> None:
        self._compute = compute_engine_policy
        registry = StaticProjectionRegistry()
        self._projection_descriptors = dict(
            projection_descriptors
            if projection_descriptors is not None
            else registry.all_descriptors()
        )
        self._overlap_classifications = list(
            overlap_classifications
            if overlap_classifications is not None
            else registry.all_overlap_classifications()
        )

    def replay_equivalence(  # noqa: C901
        self,
        *,
        command: GenerateDossierCommand,
        timestamp_ns: int,
        version_id: str,
        snapshot_data: Mapping[str, Any],
        events: Iterable[AuditEvent],
        outbox_entries: Iterable[OutboxEntry],
        projection_version: str = "pcl-v1",
    ) -> ReplayEquivalenceResult:
        failures: list[str] = []
        governance_trace: list[str] = []

        # Recompute deterministic engine output.
        engine_out = self._compute(command)

        # Replay must use version-pinned IR deserialization to ensure equivalence is independent of guard evolution.
        # Extract pinned reasoning_version_id from persisted snapshot metadata (the stored canonical IR dict).
        reasoning_version_id_raw = snapshot_data.get("reasoning_version_id")
        if (
            not isinstance(reasoning_version_id_raw, str)
            or not reasoning_version_id_raw
        ):
            failures.append("Missing/invalid reasoning_version_id in snapshot_data")
            return ReplayEquivalenceResult(
                ok=False,
                failures=tuple(failures),
                governance_trace=tuple(governance_trace),
            )
        reasoning_version_id = reasoning_version_id_raw

        try:
            canonical_ir = deserialize_to_canonical_ir(
                untrusted_payload=dict(engine_out.data),
                version_id=f"ir-v1-{command.engine_version}",
                reasoning_version_id=reasoning_version_id,
            )
            derived_snapshot = canonical_ir.to_dict()
        except CanonicalIRDeserializationError as exc:
            failures.append(f"Canonical IR deserialization in replay failed: {exc}")
            return ReplayEquivalenceResult(
                ok=False,
                failures=tuple(failures),
                governance_trace=tuple(governance_trace),
            )

        # CBI-0 governance checkpoint: replay must include the same mandatory
        # admission/projection/audit structure as live execution.
        governance_trace.extend(["M2", "M1", "M4", "SWEEP"])
        try:
            enforce_cbi0_runtime_chain_for_legal_ir(
                ir=canonical_ir,
                descriptors=self._projection_descriptors,
                overlap_classifications=self._overlap_classifications,
                runtime_label="replay",
            )
        except (RegistryValidationError, ProjectionBindingError) as exc:
            failures.append(f"CBI-0 replay governance validation failed: {exc}")
            return ReplayEquivalenceResult(
                ok=False,
                failures=tuple(failures),
                governance_trace=tuple(governance_trace),
            )

        # Gate 5 bounded invariance: replay semantic truth is determined by
        # π_O projection equivalence (≡_O), not raw structural equality of the
        # persisted snapshot representation.
        # Structural drift is therefore not a semantic failure condition here.

        events_list = list(events)
        outbox_list = list(outbox_entries)

        # Validate events identities + payload determinism.
        events_by_seq = sorted(events_list, key=lambda e: e.event_seq)
        if [e.event_seq for e in events_by_seq] != [0, 1]:
            failures.append("Unexpected event_seq set")

        # Canonical envelope reconstruction for deterministic, replay-stable identity validation.
        envelope = build_canonical_event_envelope(
            command=command,
            timestamp_ns=timestamp_ns,
            producer_identity=DEFAULT_PRODUCER_IDENTITY,
        )
        envelope_fields = canonical_event_envelope_payload(envelope)

        for ev in events_by_seq:
            expected_event_id = stable_event_id_from_envelope_components(
                organization_id=command.organization_id,
                case_id=command.case_id,
                version_id=version_id,
                event_type=ev.event_type,
                event_index=ev.event_seq,
                timestamp_ns=timestamp_ns,
                causality_id=command.causality_id,
                event_schema_version=ev.event_schema_version,
                event_seq=ev.event_seq,
                envelope_id=envelope.envelope_id,
                correlation_id=envelope.correlation_id,
                producer_identity=envelope.producer_identity,
                envelope_schema_version=envelope.envelope_schema_version,
            )
            if ev.event_id != expected_event_id:
                failures.append(f"event_id mismatch for {ev.event_type}")

            # Validate embedded canonical envelope fields are present + correct.
            if not isinstance(ev.payload, Mapping):
                failures.append(f"event payload not a mapping for {ev.event_type}")
            else:
                for k, v in envelope_fields.items():
                    if ev.payload.get(k) != v:
                        failures.append(
                            f"event {ev.event_type} envelope field mismatch: {k}"
                        )

        # Validate outbox_id identities (outbox entries are derived from DOSSIER_GENERATED).
        if len(outbox_list) != 1:
            failures.append("Unexpected outbox entry count")
        else:
            ob = outbox_list[0]

            expected_outbox_id = stable_event_id_from_envelope_components(
                organization_id=command.organization_id,
                case_id=command.case_id,
                version_id=version_id,
                event_type="OUTBOX",
                event_index=0,
                timestamp_ns=timestamp_ns,
                causality_id=command.causality_id,
                event_schema_version="v0",
                event_seq=0,
                envelope_id=envelope.envelope_id,
                correlation_id=envelope.correlation_id,
                producer_identity=envelope.producer_identity,
                envelope_schema_version=envelope.envelope_schema_version,
            )

            if ob.outbox_id is None:  # defensive; should never happen
                failures.append("Missing outbox_id")
            if ob.outbox_id != expected_outbox_id:
                failures.append("outbox_id mismatch")

            if not isinstance(ob.payload, Mapping):
                failures.append("outbox payload not a mapping")
            else:
                for k, v in envelope_fields.items():
                    if ob.payload.get(k) != v:
                        failures.append(f"outbox envelope field mismatch: {k}")

        # Gate 5 evaluator-invariant semantic check:
        # semantic equivalence compares only observable projections and excludes
        # evaluator metadata from semantic identity.
        committed_envelope = PI_O.project_from_artifacts(
            command=command,
            snapshot_data=snapshot_data,
            events=events_list,
            outbox_entries=outbox_list,
            projection_version=projection_version,
        )
        replayed_envelope = PI_O.project_from_artifacts(
            command=command,
            snapshot_data=derived_snapshot,
            events=events_list,
            outbox_entries=outbox_list,
            projection_version=projection_version,
        )
        if not EQUIV_O.equivalent(committed_envelope, replayed_envelope):
            failures.append("Observable semantic mismatch")

        # BIOK replay adversarial checks (envelope-level, after IR canonical form is established).
        try:
            validate_envelope_contract(committed_envelope)
            validate_envelope_contract(replayed_envelope)
            # Evaluator spoofing tolerance: semantic-neutral metadata changes must not
            # alter projected meaning.
            spoofed = evaluator_metadata_rename_transform(replayed_envelope)
            if not EQUIV_O.equivalent(replayed_envelope, spoofed):
                failures.append("Evaluator spoofing changed semantic projection")
        except ValueError as exc:
            failures.append(f"Replay envelope contract validation failed: {exc}")

        return ReplayEquivalenceResult(
            ok=not failures,
            failures=tuple(failures),
            governance_trace=tuple(governance_trace),
        )
