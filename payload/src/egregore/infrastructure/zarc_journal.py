from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from egregore.domain.semantics.derivations import (
    journal_deserialize_audit_events,
    journal_deserialize_outbox_entries,
)
from egregore.domain.semantics_models import (
    AuditEvent,
    CaseState,
    CommandAck,
    CommandResult,
    GenerateDossierCommand,
    OutboxEntry,
)
from egregore.interface.semantics_ports import (
    ICaseStore,
    IIdempotencyStore,
    ITransactionalPersistence,
)
from egregore.interface.zarc_journal_ports import ICommitJournal
from egregore.kernel.provenance import Provenance

JOURNAL_ENGINE = "egregore_journal"
EVENT_COMMIT_GENERATE_T2 = "commit_generate_t2"
EVENT_TERMINAL_GUARD = "terminal_guard"


@dataclass(frozen=True)
class JournalCaseView:
    state: CaseState
    next_version: int


class ZarcJournal(
    ITransactionalPersistence, IIdempotencyStore, ICaseStore, ICommitJournal
):
    """
    Atomic append-only persistence using the existing signed `.zarc` chain.

    - commit_generate_t2() appends one `.zarc` entry that contains all persisted
      artifacts needed for replay: snapshot payload, audit events, outbox entries.
    - In-memory read models (idempotency + case store + snapshot/events/outbox caches)
      are rebuilt deterministically by replaying the `.zarc` chain on startup.
    """

    def __init__(
        self,
        *,
        zarc_path: str | Path,
        signing_key_hex: str,
        prev_hash_init: str | None = None,
    ) -> None:
        self.provenance = Provenance(
            zarc_path,
            signing_key_hex=signing_key_hex,
            prev_hash_init=prev_hash_init,
            # Fail closed: never allow wall-clock timestamps through the journal.
            now_ns=lambda: (_ for _ in ()).throw(
                RuntimeError("ZarcJournal requires deterministic ts_ns injection")
            ),
        )

        self._success_by_fingerprint: dict[str, CommandResult] = {}
        self._case_view_by_key: dict[tuple[str, str], JournalCaseView] = {}

        # replay caches
        self._snapshot_by_execution_id: dict[str, Mapping[str, Any]] = {}
        self._events_by_execution_id: dict[str, tuple[AuditEvent, ...]] = {}
        self._outbox_by_execution_id: dict[str, tuple[OutboxEntry, ...]] = {}

        # terminal guard (optional, but persisted for M3 collateral)
        self._terminal_fingerprints: set[str] = set()

        self._load_state_from_chain()

    # -----------------------------
    # Seed helpers (used by HTTP layer)
    # -----------------------------

    def seed_case(
        self, *, organization_id: str, case_id: str, state: CaseState, next_version: int
    ) -> None:
        self._case_view_by_key[(organization_id, case_id)] = JournalCaseView(
            state=state,
            next_version=next_version,
        )

    # -----------------------------
    # ICaseStore
    # -----------------------------

    def get_case_state(self, *, organization_id: str, case_id: str) -> str:
        return self._case_view_by_key[(organization_id, case_id)].state.value

    def get_next_version_number(self, *, organization_id: str, case_id: str) -> int:
        return self._case_view_by_key[(organization_id, case_id)].next_version

    # -----------------------------
    # IIdempotencyStore
    # -----------------------------

    def get_success_result(self, *, input_fingerprint: str) -> CommandResult | None:
        return self._success_by_fingerprint.get(input_fingerprint)

    def put_success_result(
        self, *, input_fingerprint: str, result: CommandResult
    ) -> None:
        # The journal is the persistence truth; put_success_result should only be used
        # when state is already consistent and durable (i.e., after commit_generate_t2).
        self._success_by_fingerprint[input_fingerprint] = result

    # -----------------------------
    # ICommitJournal
    # -----------------------------

    def get_committed_snapshot(self, *, execution_id: str) -> Mapping[str, Any] | None:
        return self._snapshot_by_execution_id.get(execution_id)

    def get_committed_events(self, *, execution_id: str) -> Sequence[AuditEvent] | None:
        return self._events_by_execution_id.get(execution_id)

    def get_committed_outbox_entries(
        self, *, execution_id: str
    ) -> Sequence[OutboxEntry] | None:
        return self._outbox_by_execution_id.get(execution_id)

    # -----------------------------
    # ITransactionalPersistence
    # -----------------------------

    def commit_generate_t2(
        self,
        *,
        command: GenerateDossierCommand,
        computed_data: Mapping[str, Any],
        version_number: int,
        version_id: str,
        case_next_state: str,
        events: Iterable[AuditEvent],
        outbox_entries: Iterable[OutboxEntry],
        idempotency_fingerprint: str,
        usage_deltas: Iterable[tuple[str, str, int]],
        timestamp_ns: int,
    ) -> CommandAck:
        if timestamp_ns is None:  # fail-closed; should never happen from core executor
            raise RuntimeError(
                "ZarcJournal commit_generate_t2 requires deterministic timestamp_ns"
            )

        existing = self.get_success_result(input_fingerprint=idempotency_fingerprint)
        if existing is not None:
            # Executor already handles idempotency, but keep semantics safe.
            return CommandAck(http_status=200, result=existing, outbox_ids=None)

        events_seq = events if isinstance(events, tuple) else tuple(events)
        outbox_seq = (
            outbox_entries
            if isinstance(outbox_entries, tuple)
            else tuple(outbox_entries)
        )

        # Persist everything as payload; chain signature covers canonical JSON serialization.
        payload = {
            "execution_id": idempotency_fingerprint,
            "command": {
                "organization_id": command.organization_id,
                "case_id": command.case_id,
                "engine_version": command.engine_version,
                "policy_version": command.policy_version,
                "input_fingerprint": command.input_fingerprint,
                "causality_id": command.causality_id,
            },
            "version": {
                "version_id": version_id,
                "version_number": version_number,
                "case_next_state": case_next_state,
                "next_version": version_number + 1,
            },
            "snapshot_data": dict(computed_data),
            "events": [dataclasses.asdict(e) for e in events_seq],
            "outbox_entries": [dataclasses.asdict(o) for o in outbox_seq],
            "usage_deltas": list(usage_deltas),
        }

        self.provenance.append(
            engine=JOURNAL_ENGINE,
            event=EVENT_COMMIT_GENERATE_T2,
            payload=payload,
            ts_ns=timestamp_ns,
        )

        # Apply durable write to read models.
        result = CommandResult(
            organization_id=command.organization_id,
            case_id=command.case_id,
            version_id=version_id,
            version_number=version_number,
            engine_version=command.engine_version,
            policy_version=command.policy_version,
            data=dict(computed_data),
        )
        self._apply_commit_payload(
            idempotency_fingerprint=idempotency_fingerprint,
            command=command,
            version_number=version_number,
            version_id=version_id,
            case_next_state=case_next_state,
            events_seq=events_seq,
            outbox_seq=outbox_seq,
            snapshot_data=dict(computed_data),
            result=result,
        )

        return CommandAck(
            http_status=200,
            result=result,
            outbox_ids=[o.outbox_id for o in outbox_seq],
        )

    # -----------------------------
    # Internal: chain replay
    # -----------------------------

    def _load_state_from_chain(self) -> None:
        for entry in self.provenance.iter_entries():
            if entry.engine != JOURNAL_ENGINE:
                continue

            if entry.event == EVENT_COMMIT_GENERATE_T2:
                self._apply_commit_payload_from_journal(entry.payload)
            elif entry.event == EVENT_TERMINAL_GUARD:
                fp = entry.payload.get("fingerprint")
                if isinstance(fp, str):
                    self._terminal_fingerprints.add(fp)

    def _apply_commit_payload_from_journal(self, payload: Mapping[str, Any]) -> None:
        exec_id = payload.get("execution_id")
        if not isinstance(exec_id, str):
            return

        cmd = payload.get("command")
        version = payload.get("version")
        snapshot_data = payload.get("snapshot_data")

        if (
            not isinstance(cmd, Mapping)
            or not isinstance(version, Mapping)
            or not isinstance(snapshot_data, Mapping)
        ):
            return

        org_id = cmd.get("organization_id")
        case_id = cmd.get("case_id")
        engine_version = cmd.get("engine_version")
        policy_version = cmd.get("policy_version")
        input_fingerprint = cmd.get("input_fingerprint")
        causality_id = cmd.get("causality_id")

        version_id = version.get("version_id")
        version_number = version.get("version_number")
        case_next_state = version.get("case_next_state")
        next_version = version.get("next_version")

        if not (
            isinstance(org_id, str)
            and isinstance(case_id, str)
            and isinstance(engine_version, str)
            and isinstance(policy_version, str)
            and isinstance(input_fingerprint, str)
            and isinstance(causality_id, str)
            and isinstance(version_id, str)
            and isinstance(version_number, int)
            and isinstance(case_next_state, str)
            and isinstance(next_version, int)
        ):
            return

        events_raw = payload.get("events")
        outbox_raw = payload.get("outbox_entries")
        if not isinstance(events_raw, list) or not isinstance(outbox_raw, list):
            return

        events_seq: tuple[AuditEvent, ...] = journal_deserialize_audit_events(
            serialized_events=events_raw
        )
        outbox_seq: tuple[OutboxEntry, ...] = journal_deserialize_outbox_entries(
            serialized_outboxes=outbox_raw
        )

        command = GenerateDossierCommand(
            organization_id=org_id,
            case_id=case_id,
            actor_id="journal_replay_actor",
            input_fingerprint=input_fingerprint,
            engine_version=engine_version,
            policy_version=policy_version,
            input_payload={},  # journal replay view; snapshot carries computed_data anyway
            causality_id=causality_id,
            request_id=None,
        )

        result = CommandResult(
            organization_id=org_id,
            case_id=case_id,
            version_id=version_id,
            version_number=version_number,
            engine_version=engine_version,
            policy_version=policy_version,
            data=dict(snapshot_data),
        )

        self._apply_commit_payload(
            idempotency_fingerprint=exec_id,
            command=command,
            version_number=version_number,
            version_id=version_id,
            case_next_state=case_next_state,
            events_seq=events_seq,
            outbox_seq=outbox_seq,
            snapshot_data=dict(snapshot_data),
            result=result,
            next_version_override=next_version,
        )

    def _apply_commit_payload(
        self,
        *,
        idempotency_fingerprint: str,
        command: GenerateDossierCommand,
        version_number: int,
        version_id: str,
        case_next_state: str,
        events_seq: tuple[AuditEvent, ...],
        outbox_seq: tuple[OutboxEntry, ...],
        snapshot_data: Mapping[str, Any],
        result: CommandResult,
        next_version_override: int | None = None,
    ) -> None:
        self._success_by_fingerprint[idempotency_fingerprint] = result
        self._snapshot_by_execution_id[idempotency_fingerprint] = dict(snapshot_data)
        self._events_by_execution_id[idempotency_fingerprint] = events_seq
        self._outbox_by_execution_id[idempotency_fingerprint] = outbox_seq

        next_version = (
            next_version_override
            if next_version_override is not None
            else version_number + 1
        )
        self._case_view_by_key[(command.organization_id, command.case_id)] = (
            JournalCaseView(
                state=CaseState(case_next_state),
                next_version=next_version,
            )
        )
