from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CaseState(StrEnum):
    created = "created"
    active = "active"
    generating = "generating"
    versioned = "versioned"
    archived = "archived"


class TaskExecutionState(StrEnum):
    INIT = "INIT"
    VALIDATE = "VALIDATE"
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    COMMIT = "COMMIT"
    ARCHIVE = "ARCHIVE"


@dataclass(frozen=True)
class TaskContract:
    task_id: str
    intent: str
    constraints: tuple[str, ...]
    inputs: Mapping[str, Any]
    allowed_tools: tuple[str, ...]
    policy_level: str
    expected_outputs: tuple[str, ...]
    replayable: bool


class StableErrorCode(StrEnum):
    VALIDATION_FAILED = "VALIDATION_FAILED"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN_STATE_TRANSITION = "FORBIDDEN_STATE_TRANSITION"
    RATE_LIMITED = "RATE_LIMITED"
    ENGINE_FAILED = "ENGINE_FAILED"
    STORAGE_COMMIT_FAILED = "STORAGE_COMMIT_FAILED"
    IDempotency_FAILED = (
        "IDEMPOTENCY_FAILED"  # typo kept stable for diagnostics (not user-facing)
    )


@dataclass(frozen=True)
class SemanticsError(Exception):
    code: StableErrorCode
    message: str


@dataclass(frozen=True)
class GenerateDossierCommand:
    organization_id: str
    case_id: str
    actor_id: str

    # Deterministic identity of the request intent.
    input_fingerprint: str

    # Deterministic versions recorded for replay.
    engine_version: str
    policy_version: str

    # Canonical inputs for deterministic compute.
    input_payload: Mapping[str, Any]

    # Causality correlation (command id).
    causality_id: str

    # Input provided by ingress-plane; executor treats it as a hint only.
    request_id: str | None = None

    def to_task_contract(self) -> TaskContract:
        return TaskContract(
            task_id=self.causality_id,
            intent="generate_dossier",
            constraints=("deterministic", "fail_closed", "idempotent"),
            inputs={
                "organization_id": self.organization_id,
                "case_id": self.case_id,
                "input_fingerprint": self.input_fingerprint,
                "engine_version": self.engine_version,
                "policy_version": self.policy_version,
            },
            allowed_tools=(),
            policy_level="strict",
            expected_outputs=("snapshot", "audit_events", "outbox_entries"),
            replayable=True,
        )


@dataclass(frozen=True)
class DossierSnapshot:
    organization_id: str
    case_id: str
    version_number: int
    version_id: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class AuditEvent:
    organization_id: str
    case_id: str
    version_id: str
    event_type: str
    event_id: str
    timestamp_ns: int

    # Semantic closure requirements:
    # - event_schema_version makes event storage/replay contract explicit.
    # - event_seq is replay-reconstructable logical time for ordering/canonical replay.
    event_schema_version: str
    event_seq: int

    causality_id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class OutboxEntry:
    organization_id: str
    case_id: str
    version_id: str
    causality_id: str
    side_effect_type: str
    outbox_id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class UsageDelta:
    organization_id: str
    counter_name: str
    delta: int


@dataclass(frozen=True)
class CommandResult:
    organization_id: str
    case_id: str
    version_id: str
    version_number: int
    engine_version: str
    policy_version: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class CommandAck:
    http_status: int  # 200 or 202
    result: CommandResult
    outbox_ids: list[str] | None = None
