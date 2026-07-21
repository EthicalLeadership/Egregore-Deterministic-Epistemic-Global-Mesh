from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from egregore.domain.semantics_models import (
    AuditEvent,
    CaseState,
    CommandAck,
    CommandResult,
    GenerateDossierCommand,
    OutboxEntry,
)
from egregore.interface.semantics_ports import (
    IAuthzProvider,
    ICaseStore,
    IIdempotencyStore,
    ITransactionalPersistence,
)


class AllowAllAuthzProvider(IAuthzProvider):
    def authorize_generate(self, *, command: GenerateDossierCommand) -> None:
        return None


@dataclass
class InMemoryCaseStore(ICaseStore):
    _state_by_key: dict[tuple[str, str], str] = field(default_factory=dict)
    _next_version_by_key: dict[tuple[str, str], int] = field(default_factory=dict)

    def seed(
        self, *, organization_id: str, case_id: str, state: CaseState, next_version: int
    ) -> None:
        self._state_by_key[(organization_id, case_id)] = state.value
        self._next_version_by_key[(organization_id, case_id)] = next_version

    def get_case_state(self, *, organization_id: str, case_id: str) -> str:
        return self._state_by_key[(organization_id, case_id)]

    def get_next_version_number(self, *, organization_id: str, case_id: str) -> int:
        return self._next_version_by_key[(organization_id, case_id)]


@dataclass
class InMemoryIdempotencyStore(IIdempotencyStore):
    _success_by_fingerprint: dict[str, CommandResult] = field(default_factory=dict)

    def get_success_result(self, *, input_fingerprint: str) -> CommandResult | None:
        return self._success_by_fingerprint.get(input_fingerprint)

    def put_success_result(
        self, *, input_fingerprint: str, result: CommandResult
    ) -> None:
        self._success_by_fingerprint[input_fingerprint] = result


@dataclass
class InMemoryTransactionalPersistence(ITransactionalPersistence):
    idempotency: InMemoryIdempotencyStore
    case_store: InMemoryCaseStore
    fail_on_commit: bool = False

    commit_count: int = 0
    snapshots: list[tuple[str, str, int, str, Mapping[str, Any]]] = field(
        default_factory=list
    )
    events: list[AuditEvent] = field(default_factory=list)
    outbox: list[OutboxEntry] = field(default_factory=list)
    usage: list[tuple[str, str, int]] = field(default_factory=list)

    def commit_generate_t2(
        self,
        *,
        command: GenerateDossierCommand,
        computed_data: Mapping,
        version_number: int,
        version_id: str,
        case_next_state: str,
        events: Iterable[AuditEvent],
        outbox_entries: Iterable[OutboxEntry],
        idempotency_fingerprint: str,
        usage_deltas: Iterable[tuple[str, str, int]],
        timestamp_ns: int,
    ) -> CommandAck:
        if self.fail_on_commit:
            raise RuntimeError("Simulated T2 commit failure")

        self.commit_count += 1

        # Preserve the input order while avoiding unnecessary allocations when
        # callers already provide tuples/lists (as they do in the core executor).
        events_seq = events if isinstance(events, tuple) else tuple(events)
        outbox_seq = (
            outbox_entries
            if isinstance(outbox_entries, tuple)
            else tuple(outbox_entries)
        )

        # Avoid copying computed_data twice.
        snapshot_data = (
            computed_data if isinstance(computed_data, dict) else dict(computed_data)
        )

        self.snapshots.append(
            (
                command.organization_id,
                command.case_id,
                version_number,
                version_id,
                snapshot_data,
            )
        )
        self.events.extend(events_seq)
        self.outbox.extend(outbox_seq)
        self.usage.extend(usage_deltas)

        result = CommandResult(
            organization_id=command.organization_id,
            case_id=command.case_id,
            version_id=version_id,
            version_number=version_number,
            engine_version=command.engine_version,
            policy_version=command.policy_version,
            data=snapshot_data,
        )
        self.idempotency.put_success_result(
            input_fingerprint=idempotency_fingerprint, result=result
        )

        # Minimal monotonic progression.
        self.case_store.seed(
            organization_id=command.organization_id,
            case_id=command.case_id,
            state=CaseState(case_next_state),
            next_version=version_number + 1,
        )

        return CommandAck(
            http_status=200,
            result=result,
            outbox_ids=[o.outbox_id for o in outbox_seq],
        )
