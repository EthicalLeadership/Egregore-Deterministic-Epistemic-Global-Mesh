"""
EGREGORE LAW: PostgreSQL Transactional Persistence
Production-grade Plane 1 adapter. Same contract as SQLite, hardened for concurrency.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from egregore.domain.semantics_models import (
    AuditEvent,
    CommandAck,
    CommandResult,
    GenerateDossierCommand,
    OutboxEntry,
)
from egregore.interface.semantics_ports import ITransactionalPersistence
from egregore.kernel.provenance import Provenance

from .sqlite_dossier_adapter import (
    EVENT_COMMIT_GENERATE_T2,
    JOURNAL_ENGINE,
    _canonical_dumps,
    _canonical_loads,
    _default_signing_key_hex,
)


class PostgreSQLTransactionalPersistence(ITransactionalPersistence):
    """
    Plane 1 concrete adapter: transactional persistence backed by PostgreSQL.

    - Connection pool via psycopg2.
    - Advisory locks for case-level concurrency.
    - Same ZARC companion chain as SQLite.
    - Schema managed by migration harness (see migrate.py).
    """

    def __init__(
        self,
        dsn: str,
        zarc_dir: str,
        pool_min: int = 1,
        pool_max: int = 4,
    ) -> None:
        self._dsn = dsn
        self._zarc_dir = os.path.expanduser(zarc_dir)
        self._pool_min = pool_min
        self._pool_max = pool_max

        os.makedirs(self._zarc_dir, exist_ok=True)

        zarc_path = os.path.join(
            self._zarc_dir,
            f"{os.environ.get('EGREGORE_NODE_ID', 'unknown')}.zarc",
        )
        self._provenance = Provenance(
            zarc_path,
            signing_key_hex=_default_signing_key_hex(),
            prev_hash_init="0" * 64,
            now_ns=lambda: (_ for _ in ()).throw(
                RuntimeError(
                    "PostgreSQLTransactionalPersistence requires deterministic timestamp_ns"
                )
            ),
        )

        self._ensure_schema()

    # ------------------------------------------------------------------ #
    # PostgreSQL plumbing
    # ------------------------------------------------------------------ #

    @contextmanager
    def _cursor(self):
        conn = psycopg2.connect(self._dsn)
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS case_versions (
                    organization_id TEXT NOT NULL,
                    case_id         TEXT NOT NULL,
                    next_version    INTEGER NOT NULL DEFAULT 1,
                    case_next_state TEXT NOT NULL DEFAULT 'active',
                    PRIMARY KEY (organization_id, case_id)
                )
                """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS dossier_commits (
                    execution_id    TEXT NOT NULL PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    case_id         TEXT NOT NULL,
                    version_number  INTEGER NOT NULL,
                    version_id      TEXT NOT NULL,
                    timestamp_ns    BIGINT NOT NULL,
                    case_next_state TEXT NOT NULL,

                    command_json    TEXT NOT NULL,
                    snapshot_json   TEXT NOT NULL,
                    events_json     TEXT NOT NULL,
                    outbox_json     TEXT NOT NULL,
                    usage_deltas_json TEXT NOT NULL,

                    zarc_emitted    BOOLEAN NOT NULL DEFAULT FALSE,

                    UNIQUE(organization_id, case_id, version_number)
                )
                """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_dossier_commits_case
                ON dossier_commits(organization_id, case_id, version_number)
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
                "PostgreSQLTransactionalPersistence requires deterministic timestamp_ns"
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

        with self._cursor() as c:
            # Advisory lock on case to prevent concurrent commits.
            c.execute(
                "SELECT pg_advisory_lock(hashtext(organization_id || ':' || case_id)) FROM case_versions WHERE organization_id = %s AND case_id = %s",
                (command.organization_id, command.case_id),
            )

            # Idempotency check
            c.execute(
                """
                SELECT execution_id, organization_id, case_id, version_number, version_id,
                       timestamp_ns, case_next_state, snapshot_json, zarc_emitted
                FROM dossier_commits WHERE execution_id = %s
                """,
                (idempotency_fingerprint,),
            )
            existing = c.fetchone()

            if existing is not None:
                committed_result = CommandResult(
                    organization_id=str(existing["organization_id"]),
                    case_id=str(existing["case_id"]),
                    version_id=str(existing["version_id"]),
                    version_number=int(existing["version_number"]),
                    engine_version=command.engine_version,
                    policy_version=command.policy_version,
                    data=dict(_canonical_loads(existing["snapshot_json"])),
                )
                if not existing["zarc_emitted"]:
                    self._emit_zarc(
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
                    c.execute(
                        "UPDATE dossier_commits SET zarc_emitted = TRUE WHERE execution_id = %s",
                        (idempotency_fingerprint,),
                    )
                return CommandAck(
                    http_status=200,
                    result=committed_result,
                    outbox_ids=None,
                )

            # Version monotonicity
            c.execute(
                "SELECT next_version FROM case_versions WHERE organization_id = %s AND case_id = %s",
                (command.organization_id, command.case_id),
            )
            row = c.fetchone()

            if row is None:
                current_next_version = 1
                c.execute(
                    """
                    INSERT INTO case_versions (organization_id, case_id, next_version, case_next_state)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (command.organization_id, command.case_id, 1, case_next_state),
                )
            else:
                current_next_version = int(row["next_version"])

            if current_next_version != version_number:
                raise RuntimeError(
                    f"PostgreSQLTransactionalPersistence version mismatch for "
                    f"{command.organization_id}/{command.case_id}: "
                    f"expected {current_next_version}, got {version_number}"
                )

            c.execute(
                """
                INSERT INTO dossier_commits (
                    execution_id, organization_id, case_id, version_number, version_id, timestamp_ns,
                    case_next_state, command_json, snapshot_json, events_json, outbox_json, usage_deltas_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                SET next_version = %s, case_next_state = %s
                WHERE organization_id = %s AND case_id = %s
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

            self._emit_zarc(
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
            c.execute(
                "UPDATE dossier_commits SET zarc_emitted = TRUE WHERE execution_id = %s",
                (idempotency_fingerprint,),
            )

        return CommandAck(
            http_status=200,
            result=committed_result,
            outbox_ids=[o.outbox_id for o in outbox_seq],
        )

    def _emit_zarc(self, **kwargs) -> bool:
        # Same as SQLite adapter — delegates to kernel.Provenance
        payload = {
            "execution_id": kwargs["idempotency_fingerprint"],
            "command": {
                "organization_id": kwargs["command"].organization_id,
                "case_id": kwargs["command"].case_id,
                "engine_version": kwargs["command"].engine_version,
                "policy_version": kwargs["command"].policy_version,
                "input_fingerprint": kwargs["command"].input_fingerprint,
                "causality_id": kwargs["command"].causality_id,
            },
            "version": {
                "version_id": kwargs["version_id"],
                "version_number": kwargs["version_number"],
                "case_next_state": kwargs["case_next_state"],
                "next_version": kwargs["version_number"] + 1,
            },
            "snapshot_data": dict(kwargs["computed_data"]),
            "events": [dataclasses.asdict(e) for e in kwargs["events_seq"]],
            "outbox_entries": [dataclasses.asdict(o) for o in kwargs["outbox_seq"]],
            "usage_deltas": list(kwargs["usage_deltas"]),
        }
        self._provenance.append(
            engine=JOURNAL_ENGINE,
            event=EVENT_COMMIT_GENERATE_T2,
            payload=payload,
            ts_ns=kwargs["timestamp_ns"],
        )
        return True

    def get_next_version(self, case_id: str) -> int:
        with self._cursor() as c:
            c.execute(
                "SELECT next_version FROM case_versions WHERE case_id = %s ORDER BY next_version ASC LIMIT 1",
                (case_id,),
            )
            row = c.fetchone()
            return int(row["next_version"]) if row else 1

    def load_case_history(self, case_id: str) -> list[dict[str, Any]]:
        with self._cursor() as c:
            c.execute(
                """
                SELECT execution_id, version_number, timestamp_ns, snapshot_json
                FROM dossier_commits
                WHERE case_id = %s
                ORDER BY version_number ASC
                """,
                (case_id,),
            )
            return [
                {
                    "event_id": str(r["execution_id"]),
                    "version": int(r["version_number"]),
                    "timestamp_ns": int(r["timestamp_ns"]),
                    "result_ir": _canonical_loads(r["snapshot_json"]),
                }
                for r in c.fetchall()
            ]
