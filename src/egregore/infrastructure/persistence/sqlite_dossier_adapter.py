from __future__ import annotations

import dataclasses
import importlib
import os
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from egregore.domain.semantics_models import (
    AuditEvent,
    CommandAck,
    CommandResult,
    GenerateDossierCommand,
    OutboxEntry,
)
from egregore.interface.semantics_ports import ITransactionalPersistence
from egregore.kernel.provenance import Provenance

# Mirrors ZarcJournal engine/event names so downstream tools can treat
# this persistence as an alternative T2 backend.
JOURNAL_ENGINE = "egregore_journal"
EVENT_COMMIT_GENERATE_T2 = "commit_generate_t2"


def _default_signing_key_hex() -> str:
    key = os.environ.get("BLACKSTAR_ZARC_SIGNING_KEY_HEX")
    if not key:
        raise RuntimeError("BLACKSTAR_ZARC_SIGNING_KEY_HEX is not set")
    return key


def _canonical_module() -> Any:
    return importlib.import_module("egregore.shared.canonical")


def _canonical_dumps(obj: Any) -> str:
    return _canonical_module().canonical_dumps(obj)


def _canonical_loads(s: str | bytes | bytearray) -> Any:
    return _canonical_module().canonical_loads(s)


class SQLiteTransactionalPersistence(ITransactionalPersistence):
    """
    Plane 1 concrete adapter: transactional persistence backed by SQLite.

    - One .db file per node.
    - Companion .zarc chain is written to disk using kernel.Provenance.
    - WAL mode for concurrency and durability.
    """

    def __init__(self, db_path: str, zarc_dir: str) -> None:
        self._db_path = Path(db_path)
        self._zarc_dir = Path(zarc_dir)
        self._local = threading.local()

        self._zarc_dir.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        zarc_path = (
            self._zarc_dir / f"{os.environ.get('BLACKSTAR_NODE_ID', 'unknown')}.zarc"
        )
        self._provenance = Provenance(
            zarc_path,
            signing_key_hex=_default_signing_key_hex(),
            prev_hash_init="0" * 64,
            # Fail-closed: commit timestamp must come from core-plane timestamp_ns.
            now_ns=lambda: (_ for _ in ()).throw(
                RuntimeError(
                    "SQLiteTransactionalPersistence requires deterministic timestamp_ns"
                )
            ),
        )

        self._ensure_schema()

    # ------------------------------------------------------------------ #
    # SQLite plumbing
    # ------------------------------------------------------------------ #

    def _conn(self) -> sqlite3.Connection:
        c: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self._db_path), check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute("PRAGMA synchronous=NORMAL;")
            c.execute("PRAGMA foreign_keys=ON;")
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    def _ensure_schema(self) -> None:
        c = self._conn()
        with c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS case_versions (
                    organization_id TEXT NOT NULL,
                    case_id         TEXT NOT NULL,
                    next_version   INTEGER NOT NULL DEFAULT 1,
                    case_next_state TEXT NOT NULL DEFAULT 'active',
                    PRIMARY KEY (organization_id, case_id)
                )
                """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS dossier_commits (
                    execution_id   TEXT NOT NULL PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    case_id        TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    version_id     TEXT NOT NULL,
                    timestamp_ns   INTEGER NOT NULL,
                    case_next_state TEXT NOT NULL,

                    command_json   TEXT NOT NULL,
                    snapshot_json  TEXT NOT NULL,
                    events_json    TEXT NOT NULL,
                    outbox_json    TEXT NOT NULL,
                    usage_deltas_json TEXT NOT NULL,

                    zarc_emitted   INTEGER NOT NULL DEFAULT 0,

                    UNIQUE(organization_id, case_id, version_number)
                )
                """)

    # ------------------------------------------------------------------ #
    # Persistence contract
    # ------------------------------------------------------------------ #

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
        if timestamp_ns is None:
            raise RuntimeError(
                "SQLiteTransactionalPersistence requires deterministic timestamp_ns"
            )

        events_seq = events if isinstance(events, tuple) else tuple(events)
        outbox_seq = (
            outbox_entries
            if isinstance(outbox_entries, tuple)
            else tuple(outbox_entries)
        )

        command_payload = {
            "organization_id": command.organization_id,
            "case_id": command.case_id,
            "engine_version": command.engine_version,
            "policy_version": command.policy_version,
            "input_fingerprint": command.input_fingerprint,
            "causality_id": command.causality_id,
        }

        snapshot_json = _canonical_dumps(dict(computed_data))
        events_json = _canonical_dumps([dataclasses.asdict(e) for e in events_seq])
        outbox_json = _canonical_dumps([dataclasses.asdict(o) for o in outbox_seq])
        usage_deltas_json = _canonical_dumps(list(usage_deltas))

        # T2 atomic boundary for SQLite writes.
        c = self._conn()
        committed_result: CommandResult | None = None
        zarc_emitted_now = False

        with c:
            existing = c.execute(
                """
                SELECT execution_id, organization_id, case_id, version_number, version_id, timestamp_ns,
                       case_next_state, snapshot_json, zarc_emitted
                FROM dossier_commits WHERE execution_id = ?
                """,
                (idempotency_fingerprint,),
            ).fetchone()

            if existing is not None:
                # Fail-closed idempotency: return durable row if already committed.
                committed_result = CommandResult(
                    organization_id=str(existing["organization_id"]),
                    case_id=str(existing["case_id"]),
                    version_id=str(existing["version_id"]),
                    version_number=int(existing["version_number"]),
                    engine_version=command.engine_version,
                    policy_version=command.policy_version,
                    data=dict(_canonical_loads(existing["snapshot_json"])),
                )

                # Attempt to emit zarc if it hasn't been emitted yet.
                if int(existing["zarc_emitted"]) == 0:
                    zarc_emitted_now = self._emit_zarc(
                        command=command,
                        version_number=version_number,
                        version_id=version_id,
                        case_next_state=case_next_state,
                        computed_data=computed_data,
                        events_seq=events_seq,
                        outbox_seq=outbox_seq,
                        usage_deltas=usage_deltas,
                        idempotency_fingerprint=idempotency_fingerprint,
                        timestamp_ns=timestamp_ns,
                    )
                    if zarc_emitted_now:
                        c.execute(
                            "UPDATE dossier_commits SET zarc_emitted = 1 WHERE execution_id = ?",
                            (idempotency_fingerprint,),
                        )

                return CommandAck(
                    http_status=200,
                    result=committed_result,
                    outbox_ids=None,
                )

            # Ensure case_versions row exists + enforce monotonicity.
            row = c.execute(
                "SELECT next_version FROM case_versions WHERE organization_id = ? AND case_id = ?",
                (command.organization_id, command.case_id),
            ).fetchone()

            if row is None:
                current_next_version = 1
                c.execute(
                    """
                    INSERT INTO case_versions (organization_id, case_id, next_version, case_next_state)
                    VALUES (?, ?, ?, ?)
                    """,
                    (command.organization_id, command.case_id, 1, case_next_state),
                )
            else:
                current_next_version = int(row["next_version"])

            if current_next_version != version_number:
                raise RuntimeError(
                    f"SQLiteTransactionalPersistence version mismatch for "
                    f"{command.organization_id}/{command.case_id}: "
                    f"expected {current_next_version}, got {version_number}"
                )

            c.execute(
                """
                INSERT INTO dossier_commits (
                    execution_id, organization_id, case_id, version_number, version_id, timestamp_ns,
                    case_next_state, command_json, snapshot_json, events_json, outbox_json, usage_deltas_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_fingerprint,
                    command.organization_id,
                    command.case_id,
                    version_number,
                    version_id,
                    timestamp_ns,
                    case_next_state,
                    _canonical_dumps(command_payload),
                    snapshot_json,
                    events_json,
                    outbox_json,
                    usage_deltas_json,
                ),
            )

            c.execute(
                """
                UPDATE case_versions
                SET next_version = ?, case_next_state = ?
                WHERE organization_id = ? AND case_id = ?
                """,
                (
                    version_number + 1,
                    case_next_state,
                    command.organization_id,
                    command.case_id,
                ),
            )

            committed_result = CommandResult(
                organization_id=command.organization_id,
                case_id=command.case_id,
                version_id=version_id,
                version_number=version_number,
                engine_version=command.engine_version,
                policy_version=command.policy_version,
                data=dict(computed_data),
            )

            # ZARC emission is outside SQLite atomicity, but we still keep it fail-closed:
            # if it fails, raising here aborts the transaction (no commit row persisted).
            # If the process crashes after writing to disk but before commit, retry
            # will see sqlite row and re-emit only if zarc_emitted=0.
            zarc_emitted_now = self._emit_zarc(
                command=command,
                version_number=version_number,
                version_id=version_id,
                case_next_state=case_next_state,
                computed_data=computed_data,
                events_seq=events_seq,
                outbox_seq=outbox_seq,
                usage_deltas=usage_deltas,
                idempotency_fingerprint=idempotency_fingerprint,
                timestamp_ns=timestamp_ns,
            )
            if zarc_emitted_now:
                c.execute(
                    "UPDATE dossier_commits SET zarc_emitted = 1 WHERE execution_id = ?",
                    (idempotency_fingerprint,),
                )

        return CommandAck(
            http_status=200,
            result=(
                committed_result
                if committed_result is not None
                else CommandResult(
                    organization_id=command.organization_id,
                    case_id=command.case_id,
                    version_id=version_id,
                    version_number=version_number,
                    engine_version=command.engine_version,
                    policy_version=command.policy_version,
                    data=dict(computed_data),
                )
            ),
            outbox_ids=[o.outbox_id for o in outbox_seq],
        )

    def _emit_zarc(
        self,
        *,
        command: GenerateDossierCommand,
        version_number: int,
        version_id: str,
        case_next_state: str,
        computed_data: Mapping,
        events_seq: tuple[AuditEvent, ...],
        outbox_seq: tuple[OutboxEntry, ...],
        usage_deltas: Iterable[tuple[str, str, int]],
        idempotency_fingerprint: str,
        timestamp_ns: int,
    ) -> bool:
        # Build payload compatible with ZarcJournal.
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

        # Non-reentry guard collateral: idempotency_fingerprint is embedded as
        # ZarcJournal payload.execution_id. Signature/hash chain is handled by kernel.Provenance.

        self._provenance.append(
            engine=JOURNAL_ENGINE,
            event=EVENT_COMMIT_GENERATE_T2,
            payload=payload,
            ts_ns=timestamp_ns,
        )
        return True

    # ------------------------------------------------------------------ #
    # Additional local query surface (Phase 1 test contract helpers)
    # ------------------------------------------------------------------ #

    def get_next_version(self, case_id: str) -> int:
        """
        Phase-1 helper: return next_version for the first organization row that matches case_id.

        (The system proper keys versions by (organization_id, case_id), but the Phase-1
        test contract only passes case_id.)
        """
        row = (
            self._conn()
            .execute(
                """
            SELECT next_version
            FROM case_versions
            WHERE case_id = ?
            ORDER BY next_version ASC
            LIMIT 1
            """,
                (case_id,),
            )
            .fetchone()
        )
        return int(row["next_version"]) if row is not None else 1

    def load_case_history(self, case_id: str) -> list[dict[str, Any]]:
        rows = (
            self._conn()
            .execute(
                """
            SELECT execution_id, version_number, timestamp_ns, snapshot_json
            FROM dossier_commits
            WHERE case_id = ?
            ORDER BY version_number ASC
            """,
                (case_id,),
            )
            .fetchall()
        )

        return [
            {
                "event_id": str(r["execution_id"]),
                "version": int(r["version_number"]),
                "timestamp_ns": int(r["timestamp_ns"]),
                "result_ir": _canonical_loads(r["snapshot_json"]),
            }
            for r in rows
        ]
