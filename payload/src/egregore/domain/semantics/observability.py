from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from egregore.domain.semantics_models import (
    AuditEvent,
    GenerateDossierCommand,
    OutboxEntry,
)
from egregore.shared.canonical import canonical_json, sha256_hex

_EXECUTION_CORE_KEYS: frozenset[str] = frozenset(
    {
        "snapshot_hash",
        "event_ids",
        "event_seq",
        "outbox_ids",
        "admissibility_classification",
    }
)
_CASE_CORE_KEYS: frozenset[str] = frozenset(
    {
        "organization_id",
        "case_id",
        "causality_attribution_class",
        "archive_stability_classification",
    }
)
_POLICY_CORE_KEYS: frozenset[str] = frozenset(
    {
        "policy_version",
        "policy_level",
        "routing_outcome_class",
    }
)

_EXECUTION_EXTENSION_KEYS: frozenset[str] = frozenset({"x_execution"})
_CASE_EXTENSION_KEYS: frozenset[str] = frozenset({"x_case"})
_POLICY_EXTENSION_KEYS: frozenset[str] = frozenset({"x_policy"})

_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {"hidden_state", "_internal", "_fsm", "_canonical"}
)


class PolicyRoutingOutcomeClass(StrEnum):
    ALLOW = "ALLOW"
    QUARANTINE = "QUARANTINE"
    FAIL = "FAIL"


class SemanticRelevanceClass(StrEnum):
    ADMISSIBILITY_CLASSIFICATION = "admissibility_classification"
    ARCHIVE_STABILITY_CLASSIFICATION = "archive_stability_classification"
    POLICY_ROUTING_OUTCOME_CLASS = "policy_routing_outcome_class"
    CAUSALITY_ATTRIBUTION_CLASS = "causality_attribution_class"


@dataclass(frozen=True)
class EvaluatorMetadata:
    engine_version: str
    policy_version: str
    projection_version: str
    # Explicit partition: fields listed here are semantic-affecting and must be mapped into pi.
    semantic_affecting_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservableEnvelope:
    execution: Mapping[str, Any]
    case: Mapping[str, Any]
    policy: Mapping[str, Any]
    evaluator: EvaluatorMetadata


@dataclass(frozen=True)
class SemanticClassification:
    admissibility_classification: str
    archive_stability_classification: str
    policy_routing_outcome_class: PolicyRoutingOutcomeClass
    causality_attribution_class: str


@dataclass(frozen=True)
class ProjectionOperator:
    """
    Canonical projection operator pi_O.

    All runtime components must use this object for envelope construction.
    """

    projection_id: str = "pi_O"

    def project_from_artifacts(
        self,
        *,
        command: GenerateDossierCommand,
        snapshot_data: Mapping[str, Any],
        events: Iterable[AuditEvent],
        outbox_entries: Iterable[OutboxEntry],
        projection_version: str,
    ) -> ObservableEnvelope:
        return build_observable_envelope_from_artifacts(
            command=command,
            snapshot_data=snapshot_data,
            events=events,
            outbox_entries=outbox_entries,
            projection_version=projection_version,
        )

    def project(self, envelope: ObservableEnvelope) -> Mapping[str, Any]:
        return canonical_observable_payload(envelope)


@dataclass(frozen=True)
class EquivalenceRelation:
    """
    Canonical observable equivalence relation ≡_O.

    This object is immutable and shared across executor/replay/test layers.
    """

    relation_id: str = "equiv_O"

    def equivalent(self, lhs: ObservableEnvelope, rhs: ObservableEnvelope) -> bool:
        validate_envelope_contract(lhs)
        validate_envelope_contract(rhs)
        return self.projected(lhs) == self.projected(rhs)

    def projected(self, envelope: ObservableEnvelope) -> Mapping[str, Any]:
        return PI_O.project(envelope)


def evaluate_policy_routing(
    *, policy_observable_prefix: Mapping[str, Any]
) -> PolicyRoutingOutcomeClass:
    """
    Gate 5 Option A policy model:
    P = f(O(h_prefix))

    This function intentionally accepts only policy observable input.
    No external context, timing channel, or hidden runtime state is permitted.
    """
    mode = str(policy_observable_prefix.get("policy_level", "strict")).lower()
    if mode in {"strict", "allow"}:
        return PolicyRoutingOutcomeClass.ALLOW
    if mode in {"quarantine", "review"}:
        return PolicyRoutingOutcomeClass.QUARANTINE
    return PolicyRoutingOutcomeClass.FAIL


def canonical_observable_payload(envelope: ObservableEnvelope) -> Mapping[str, Any]:
    """
    Semantic identity payload.

    Evaluator metadata is intentionally excluded so semantic equivalence is evaluator-invariant.
    """
    payload = {
        "execution": dict(envelope.execution),
        "case": dict(envelope.case),
        "policy": dict(envelope.policy),
    }
    # Explicit evaluator metadata partition: only declared semantic-affecting fields
    # are permitted to influence semantic projection.
    affecting: dict[str, str] = {}
    for field in envelope.evaluator.semantic_affecting_fields:
        if field == "engine_version":
            affecting[field] = envelope.evaluator.engine_version
        elif field == "policy_version":
            affecting[field] = envelope.evaluator.policy_version
        elif field == "projection_version":
            affecting[field] = envelope.evaluator.projection_version
        else:
            raise ValueError(
                f"Unrecognized semantic-affecting evaluator field: {field}"
            )
    if affecting:
        payload["evaluator_semantic"] = affecting
    return payload


def semantic_hash(envelope: ObservableEnvelope) -> str:
    payload = canonical_observable_payload(envelope)
    return sha256_hex(canonical_json(payload).encode("utf-8"))


def projection_hash(envelope: ObservableEnvelope) -> str:
    """
    Projection hash over canonical serialized observable payloads.

    This remains evaluator-invariant by design. Evaluator metadata is governance metadata,
    not semantic identity input.
    """
    return semantic_hash(envelope)


def classify_semantics(envelope: ObservableEnvelope) -> SemanticClassification:
    execution = envelope.execution
    case_obs = envelope.case
    policy = envelope.policy

    return SemanticClassification(
        admissibility_classification=str(
            execution.get("admissibility_classification", "UNKNOWN")
        ),
        archive_stability_classification=str(
            case_obs.get("archive_stability_classification", "UNKNOWN")
        ),
        policy_routing_outcome_class=PolicyRoutingOutcomeClass(
            str(
                policy.get(
                    "routing_outcome_class", PolicyRoutingOutcomeClass.FAIL.value
                )
            )
        ),
        causality_attribution_class=str(
            case_obs.get("causality_attribution_class", "UNKNOWN")
        ),
    )


def semantically_equivalent(lhs: ObservableEnvelope, rhs: ObservableEnvelope) -> bool:
    """
    Semantic equivalence excludes evaluator metadata and compares only observable projections.
    """
    return semantic_hash(lhs) == semantic_hash(rhs)


def enforce_semantic_equivalence(
    *, lhs: ObservableEnvelope, rhs: ObservableEnvelope, reason: str
) -> None:
    if not semantically_equivalent(lhs, rhs):
        raise ValueError(f"Semantic equivalence violated: {reason}")


def is_semantically_relevant_property(name: str) -> bool:
    return name in {member.value for member in SemanticRelevanceClass}


def ensure_relevance_closed_basis(names: Iterable[str]) -> None:
    unknown = [n for n in names if not is_semantically_relevant_property(n)]
    if unknown:
        raise ValueError(f"Unknown semantic relevance properties: {unknown}")


def validate_envelope_contract(envelope: ObservableEnvelope) -> None:
    """
    BIOK bounded schema contract:
    core_keys subset keys subset (core_keys union extension_keys), with forbidden key rejection.
    """
    execution_keys = set(envelope.execution.keys())
    case_keys = set(envelope.case.keys())
    policy_keys = set(envelope.policy.keys())

    if _FORBIDDEN_KEYS.intersection(execution_keys.union(case_keys).union(policy_keys)):
        blocked = sorted(
            _FORBIDDEN_KEYS.intersection(
                execution_keys.union(case_keys).union(policy_keys)
            )
        )
        raise ValueError(f"Forbidden keys in envelope: {blocked}")

    execution_allowed = _EXECUTION_CORE_KEYS.union(_EXECUTION_EXTENSION_KEYS)
    case_allowed = _CASE_CORE_KEYS.union(_CASE_EXTENSION_KEYS)
    policy_allowed = _POLICY_CORE_KEYS.union(_POLICY_EXTENSION_KEYS)

    if not _EXECUTION_CORE_KEYS.issubset(execution_keys):
        missing = sorted(_EXECUTION_CORE_KEYS.difference(execution_keys))
        raise ValueError(f"Missing execution core keys: {missing}")
    if not _CASE_CORE_KEYS.issubset(case_keys):
        missing = sorted(_CASE_CORE_KEYS.difference(case_keys))
        raise ValueError(f"Missing case core keys: {missing}")
    if not _POLICY_CORE_KEYS.issubset(policy_keys):
        missing = sorted(_POLICY_CORE_KEYS.difference(policy_keys))
        raise ValueError(f"Missing policy core keys: {missing}")

    if not execution_keys.issubset(execution_allowed):
        invalid = sorted(execution_keys.difference(execution_allowed))
        raise ValueError(
            "Invalid execution projection keys: "
            f"core={sorted(_EXECUTION_CORE_KEYS)} extension={sorted(_EXECUTION_EXTENSION_KEYS)} invalid={invalid}"
        )
    if not case_keys.issubset(case_allowed):
        invalid = sorted(case_keys.difference(case_allowed))
        raise ValueError(
            f"Invalid case projection keys: core={sorted(_CASE_CORE_KEYS)} extension={sorted(_CASE_EXTENSION_KEYS)} invalid={invalid}"
        )
    if not policy_keys.issubset(policy_allowed):
        invalid = sorted(policy_keys.difference(policy_allowed))
        raise ValueError(
            "Invalid policy projection keys: "
            f"core={sorted(_POLICY_CORE_KEYS)} extension={sorted(_POLICY_EXTENSION_KEYS)} invalid={invalid}"
        )

    routing = str(envelope.policy["routing_outcome_class"])
    try:
        PolicyRoutingOutcomeClass(routing)
    except ValueError as exc:
        raise ValueError(f"Invalid policy routing outcome class: {routing}") from exc


def admissible_extension(
    *, base: ObservableEnvelope, extended: ObservableEnvelope
) -> bool:
    """
    Gate 5 extension admissibility predicate imported from Gate 4 semantics:
    an extension is admissible only if observable projections are preserved.
    """
    return semantically_equivalent(base, extended)


AdmissibleTransform = Callable[[ObservableEnvelope], ObservableEnvelope]


def is_admissible_step(
    *,
    current: ObservableEnvelope,
    next_envelope: ObservableEnvelope,
    relation: EquivalenceRelation,
) -> bool:
    """
    L1 step-wise admissibility model.
    """
    validate_envelope_contract(current)
    validate_envelope_contract(next_envelope)
    return relation.equivalent(current, next_envelope)


def enforce_admissible_step(
    *,
    current: ObservableEnvelope,
    next_envelope: ObservableEnvelope,
    relation: EquivalenceRelation,
    reason: str,
) -> None:
    if not is_admissible_step(
        current=current, next_envelope=next_envelope, relation=relation
    ):
        raise ValueError(f"Admissibility step failed: {reason}")


def identity_transform(envelope: ObservableEnvelope) -> ObservableEnvelope:
    return envelope


def evaluator_metadata_rename_transform(
    envelope: ObservableEnvelope,
) -> ObservableEnvelope:
    """
    Admissible transform example: evaluator metadata may vary without semantic impact.
    """
    return ObservableEnvelope(
        execution=envelope.execution,
        case=envelope.case,
        policy=envelope.policy,
        evaluator=EvaluatorMetadata(
            engine_version=f"alias:{envelope.evaluator.engine_version}",
            policy_version=f"alias:{envelope.evaluator.policy_version}",
            projection_version=envelope.evaluator.projection_version,
        ),
    )


def build_observable_envelope_from_artifacts(
    *,
    command: GenerateDossierCommand,
    snapshot_data: Mapping[str, Any],
    events: Iterable[AuditEvent],
    outbox_entries: Iterable[OutboxEntry],
    projection_version: str,
) -> ObservableEnvelope:
    events_by_seq = sorted(events, key=lambda e: e.event_seq)
    outbox_entries_sorted = sorted(outbox_entries, key=lambda o: o.outbox_id)

    policy_prefix = {
        "policy_level": command.to_task_contract().policy_level,
        "policy_version": command.policy_version,
    }
    route = evaluate_policy_routing(policy_observable_prefix=policy_prefix)

    execution_projection = {
        "snapshot_hash": sha256_hex(
            canonical_json(dict(snapshot_data)).encode("utf-8")
        ),
        "event_ids": [e.event_id for e in events_by_seq],
        "event_seq": [e.event_seq for e in events_by_seq],
        "outbox_ids": [o.outbox_id for o in outbox_entries_sorted],
        "admissibility_classification": "ADMISSIBLE",
    }

    case_projection = {
        "organization_id": command.organization_id,
        "case_id": command.case_id,
        "causality_attribution_class": "CAUSALITY_TRACEABLE",
        "archive_stability_classification": "NON_TERMINAL",
    }

    policy_projection = {
        "policy_version": command.policy_version,
        "policy_level": command.to_task_contract().policy_level,
        "routing_outcome_class": route.value,
    }

    evaluator = EvaluatorMetadata(
        engine_version=command.engine_version,
        policy_version=command.policy_version,
        projection_version=projection_version,
    )

    envelope = ObservableEnvelope(
        execution=execution_projection,
        case=case_projection,
        policy=policy_projection,
        evaluator=evaluator,
    )
    validate_envelope_contract(envelope)
    return envelope


PI_O = ProjectionOperator()
EQUIV_O = EquivalenceRelation()
