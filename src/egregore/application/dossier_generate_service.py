from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from egregore.application.semantics_executor import (
    CorePlaneGenerateDossierExecutor,
    GenerateDossierEngineResult,
)
from egregore.domain.semantics_models import CommandAck, GenerateDossierCommand


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

    # Optional override: if absent, the service deterministically derives it.
    timestamp_ns: int | None = None


def derive_timestamp_ns_deterministically(command: GenerateDossierCommand) -> int:
    """
    Core plane requires timestamp_ns for stable identity derivation.
    This helper produces a deterministic timestamp_ns from the command itself
    (no wall-clock access), so API/transport layers can omit timestamp_ns.

    Implementation detail:
    - Prior version used sha256_hex(...), took first 16 hex chars (first 8 digest bytes),
      then int(hex, 16).
    - We compute digest bytes directly and convert the first 8 bytes to an int.
      This yields the exact same numeric result while avoiding the hex-string pipeline.
    """
    raw = (
        f"{command.organization_id}|{command.case_id}|{command.actor_id}|{command.input_fingerprint}|"
        f"{command.engine_version}|{command.policy_version}|{command.causality_id}"
    )
    import hashlib

    digest = hashlib.sha256(raw.encode("utf-8")).digest()  # 32 bytes
    first_8_bytes = digest[:8]
    return int.from_bytes(first_8_bytes, byteorder="big")


class DossierGenerateService:
    """
    Transport-agnostic dossier generation service wrapper around the deterministic core plane.

    Responsibilities:
    - map external request DTO → GenerateDossierCommand
    - provide deterministic timestamp_ns if caller doesn't provide one
    - delegate to CorePlaneGenerateDossierExecutor
    """

    def __init__(self, *, executor: CorePlaneGenerateDossierExecutor) -> None:
        self._executor = executor

    def generate(
        self,
        *,
        request: DossierGenerateRequest | None = None,
        envelope: Mapping[str, Any] | None = None,
        timestamp_ns: int | None = None,
    ) -> CommandAck:
        """
        Invariant-preserving input adapter.

        - If `envelope` is provided, it is deterministically mapped to a
          `DossierGenerateRequest` via the application-layer adapter
          `execution_envelope_mapper.envelope_to_dossier_request`.
        - The core execution pathway remains unchanged:
          request/envelope → GenerateDossierCommand → executor → derive_generate_artifacts → commit_generate_t2()
        """
        if envelope is not None:
            from egregore.application.mappers.execution_envelope_mapper import (
                envelope_to_dossier_request,
            )

            request = envelope_to_dossier_request(envelope)

        if request is None:
            raise ValueError("Either request=... or envelope=... must be provided")

        command = GenerateDossierCommand(
            organization_id=request.organization_id,
            case_id=request.case_id,
            actor_id=request.actor_id,
            input_fingerprint=request.input_fingerprint,
            engine_version=request.engine_version,
            policy_version=request.policy_version,
            input_payload=request.input_payload,
            causality_id=request.causality_id,
            request_id=request.request_id,
        )

        resolved_timestamp_ns = (
            timestamp_ns if timestamp_ns is not None else request.timestamp_ns
        )
        if resolved_timestamp_ns is None:
            resolved_timestamp_ns = derive_timestamp_ns_deterministically(command)

        return self._executor.handle_generate_dossier(
            command=command, timestamp_ns=resolved_timestamp_ns
        )


def build_default_dossier_executor(
    *,
    authz: Any,
    case_store: Any,
    idempotency_store: Any,
    transactional_persistence: Any,
    compute_engine_policy: Callable[
        [GenerateDossierCommand], GenerateDossierEngineResult
    ],
) -> CorePlaneGenerateDossierExecutor:
    """
    Convenience constructor to keep wiring in one place.

    Note: types are kept as Any here to avoid importing port protocols in this file.
    """
    return CorePlaneGenerateDossierExecutor(
        authz=authz,
        case_store=case_store,
        idempotency_store=idempotency_store,
        transactional_persistence=transactional_persistence,
        compute_engine_policy=compute_engine_policy,
    )
