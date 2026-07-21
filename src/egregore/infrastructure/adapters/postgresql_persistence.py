"""
infrastructure/adapters/postgresql_persistence.py
PostgreSQL Concrete Persistence - PLANE 2.
"""

import hashlib
import logging
from contextlib import contextmanager
from dataclasses import dataclass

try:
    import psycopg

    PSYCOPG_AVAILABLE = True
except ImportError:
    PSYCOPG_AVAILABLE = False

try:
    from psycopg_pool import ConnectionPool

    PSYCOPG_POOL_AVAILABLE = True
except ImportError:
    PSYCOPG_POOL_AVAILABLE = False

from egregore.domain.models.dossier import CommitResult, Dossier
from egregore.interface.semantics_ports import ITransactionalPersistence
from egregore.shared.canonical import canonical_dumps

logger = logging.getLogger("egregore.persistence.postgresql")


@dataclass(frozen=True)
class PersistenceConfig:
    dsn: str
    min_connections: int = 1
    max_connections: int = 10
    connect_timeout: int = 5
    command_timeout: int = 30
    pool_timeout: int = 10
    max_waiting: int = 100
    schema_path: str | None = None

    def __post_init__(self):
        if not self.dsn:
            raise ValueError("dsn must be provided")
        if self.min_connections < 1:
            raise ValueError("min_connections must be >= 1")
        if self.max_connections < self.min_connections:
            raise ValueError("max_connections must be >= min_connections")
        if self.pool_timeout < 1:
            raise ValueError("pool_timeout must be >= 1")
        if self.max_waiting < 0:
            raise ValueError("max_waiting must be >= 0")

    def query_audit_log(self, case_id: str, limit: int = 100):
        if not self._initialized:
            raise RuntimeError("Persistence not initialized")
        logs = []
        with self._connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT timestamp_ns, checkpoint, operation, scope, result, dossier_id, note "
                "FROM governance_log WHERE dossier_id = %s OR note LIKE %s "
                "ORDER BY timestamp_ns DESC LIMIT %s",
                (case_id, f"%{case_id}%", limit),
            )
            for row in cur.fetchall():
                logs.append(
                    {
                        "timestamp_ns": row[0],
                        "checkpoint": row[1],
                        "operation": row[2],
                        "scope": row[3],
                        "result": row[4],
                        "dossier_id": row[5],
                        "note": row[6],
                    }
                )
        return logs


class PostgreSQLPersistence(ITransactionalPersistence):
    def __init__(self, config, governance_bridge=None, signer=None):
        if not PSYCOPG_AVAILABLE:
            raise RuntimeError("psycopg missing. pip install psycopg[binary]")
        if not PSYCOPG_POOL_AVAILABLE:
            raise RuntimeError("psycopg_pool missing. pip install psycopg_pool")
        self._config = config
        self._governance = governance_bridge
        self._signer = signer
        self._pool = None
        self._initialized = False

    def initialize(self):
        if self._initialized:
            return
        self._pool = ConnectionPool(
            self._config.dsn,
            min_size=self._config.min_connections,
            max_size=self._config.max_connections,
            timeout=self._config.pool_timeout,
            max_waiting=self._config.max_waiting,
            check=ConnectionPool.check_connection,
            kwargs={
                "connect_timeout": self._config.connect_timeout,
                "options": f"-c statement_timeout={self._config.command_timeout * 1000}",
            },
        )
        with self._connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'dossiers')"
            )
            if not cur.fetchone()[0]:
                raise RuntimeError("Schema not initialized. Run: psql -f schema.sql")
        self._initialized = True
        logger.info("PostgreSQL persistence initialized")

    def close(self):
        if self._pool is not None:
            self._pool.close()
            self._pool = None
            self._initialized = False

    @contextmanager
    def _connection(self):
        if self._pool is None:
            raise RuntimeError("Persistence not initialized")
        with self._pool.connection() as conn:
            yield conn

    @staticmethod
    def _canonical_json(obj):
        try:
            return canonical_dumps(obj, default=str)
        except TypeError as e:
            logger.warning("Non-serializable object: %s", e)
            return canonical_dumps(obj, default=str)

    def _provenance_hash(self, obj):
        return hashlib.sha256(self._canonical_json(obj).encode("utf-8")).hexdigest()

    def _sign(self, canonical_bytes):
        if self._signer is not None:
            return self._signer.sign(canonical_bytes)
        return hashlib.sha256(canonical_bytes).hexdigest()

    def _emit_governance(
        self, timestamp_ns, operation, scope, result, dossier_id=None, note=None
    ):
        if self._governance is None:
            return
        try:
            self._governance.emit(
                {
                    "timestamp_ns": timestamp_ns,
                    "checkpoint": "M4",
                    "operation": operation,
                    "scope": scope,
                    "result": result,
                    "note": note or "PostgreSQL persistence",
                    "dossier_id": dossier_id,
                }
            )
        except Exception as e:
            logger.warning("Governance bridge emission failed: %s", e)

    def _log_governance_db(
        self,
        conn,
        timestamp_ns,
        checkpoint,
        operation,
        scope,
        result,
        dossier_id=None,
        note=None,
    ):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO governance_log (timestamp_ns, checkpoint, operation, scope, result, dossier_id, note) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (timestamp_ns, checkpoint, operation, scope, result, dossier_id, note),
            )

    def _log_governance_failure(
        self, timestamp_ns, operation, scope, result, dossier_id=None, note=None
    ):
        try:
            with self._connection() as conn:
                conn.autocommit = True
                self._log_governance_db(
                    conn, timestamp_ns, "M4", operation, scope, result, dossier_id, note
                )
        except Exception as e:
            logger.error("CRITICAL: Could not log governance failure: %s", e)

    def commit_generate_t2(self, dossier: Dossier) -> CommitResult:
        if not self._initialized:
            raise RuntimeError("Persistence not initialized")
        dossier_id = dossier.dossier_id
        case_id = dossier.case_id
        intent_hash = dossier.intent_hash
        version = dossier.version
        timestamp_ns = dossier.timestamp_ns

        state_dict = dossier.state.to_dict()
        canonical_state = self._canonical_json(state_dict)
        state_bytes = canonical_state.encode("utf-8")
        dossier_signature = self._sign(state_bytes)

        events = dossier.state.events
        trace = {
            "dossier_id": dossier_id,
            "case_id": case_id,
            "version": version,
            "timestamp_ns": timestamp_ns,
            "intent_hash": intent_hash,
            "state_hash": self._provenance_hash(state_dict),
            "events": [self._canonical_json(e.to_dict()) for e in events],
        }
        trace_hash = self._provenance_hash(trace)
        try:
            with self._connection() as conn, conn.transaction():  # noqa: SIM117
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO dossiers (dossier_id, case_id, version, intent_hash, state, canonical_state, timestamp_ns, signature) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s) ON CONFLICT (dossier_id) DO UPDATE SET version = EXCLUDED.version, intent_hash = EXCLUDED.intent_hash, state = EXCLUDED.state, canonical_state = EXCLUDED.canonical_state, timestamp_ns = EXCLUDED.timestamp_ns, signature = EXCLUDED.signature",
                        (
                            dossier_id,
                            case_id,
                            version,
                            intent_hash,
                            canonical_state,
                            canonical_state,
                            timestamp_ns,
                            dossier_signature,
                        ),
                    )
                    event_count = 0
                    for seq, event in enumerate(events, start=1):
                        event_id = f"{dossier_id}:{seq}"
                        event_dict = event.to_dict()
                        payload_json = self._canonical_json(
                            event_dict.get("payload", {})
                        )
                        cur.execute(
                            "INSERT INTO events (event_id, dossier_id, event_schema_version, event_seq, event_type, payload, timestamp_ns, provenance_hash) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s) ON CONFLICT (event_id) DO NOTHING",
                            (
                                event_id,
                                dossier_id,
                                1,
                                seq,
                                event_dict.get("event_type", "UNKNOWN"),
                                payload_json,
                                timestamp_ns,
                                self._provenance_hash(event_dict.get("payload", {})),
                            ),
                        )
                        if cur.rowcount > 0:
                            event_count += 1
                    cur.execute(
                        "INSERT INTO replay_traces (dossier_id, trace_hash, timestamp_ns, verified) VALUES (%s, %s, %s, FALSE)",
                        (dossier_id, trace_hash, timestamp_ns),
                    )
                    self._log_governance_db(
                        conn,
                        timestamp_ns,
                        "M4",
                        "commit_generate_t2",
                        "persistence",
                        "EQUIVALENT",
                        dossier_id,
                        f"Committed {event_count} events, version {version}",
                    )
            self._emit_governance(
                timestamp_ns,
                "commit_generate_t2",
                "persistence",
                "EQUIVALENT",
                dossier_id=dossier_id,
                note=f"Committed {event_count} events, version {version}",
            )
            logger.info(
                "T2 commit: dossier=%s version=%d events=%d trace=%s",
                dossier_id,
                version,
                event_count,
                trace_hash[:16],
            )
            return CommitResult(
                dossier_id=dossier_id,
                version=version,
                event_count=event_count,
                trace_hash=trace_hash,
            )
        except Exception as e:
            self._log_governance_failure(
                timestamp_ns,
                "commit_generate_t2",
                "persistence",
                "DIVERGED",
                dossier_id=dossier_id,
                note=f"Transaction failed: {type(e).__name__}: {e}",
            )
            self._emit_governance(
                timestamp_ns,
                "commit_generate_t2",
                "persistence",
                "DIVERGED",
                dossier_id=dossier_id,
                note=f"Transaction failed: {type(e).__name__}: {e}",
            )
            raise

    def get_dossier(self, dossier_id):
        with (
            self._connection() as conn,
            conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
        ):
            cur.execute(
                "SELECT dossier_id, case_id, version, intent_hash, state, timestamp_ns, signature FROM dossiers WHERE dossier_id = %s",
                (dossier_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_canonical_dossier(self, dossier_id):
        with (
            self._connection() as conn,
            conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
        ):
            cur.execute(
                "SELECT dossier_id, case_id, version, intent_hash, canonical_state, timestamp_ns, signature FROM dossiers WHERE dossier_id = %s",
                (dossier_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            result = dict(row)
            result["state"] = result.pop("canonical_state")
            return result

    def get_events(self, dossier_id):
        with (
            self._connection() as conn,
            conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
        ):
            cur.execute(
                "SELECT event_id, event_schema_version, event_seq, event_type, payload, timestamp_ns, provenance_hash FROM events WHERE dossier_id = %s ORDER BY event_seq",
                (dossier_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    def verify_replay_trace(self, dossier_id, expected_trace_hash):
        with self._connection() as conn, conn.transaction():  # noqa: SIM117
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT trace_hash FROM replay_traces WHERE dossier_id = %s ORDER BY timestamp_ns DESC LIMIT 1 FOR UPDATE",
                    (dossier_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return False
                actual = row[0]
                match = actual == expected_trace_hash
                if match:
                    cur.execute(
                        "UPDATE replay_traces SET verified = TRUE WHERE dossier_id = %s AND trace_hash = %s",
                        (dossier_id, actual),
                    )
                return match

    def get_governance_log(self, dossier_id=None, checkpoint=None, limit=100):
        with (
            self._connection() as conn,
            conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
        ):
            if dossier_id and checkpoint:
                cur.execute(
                    "SELECT * FROM governance_log WHERE dossier_id = %s AND checkpoint = %s ORDER BY timestamp_ns DESC LIMIT %s",
                    (dossier_id, checkpoint, limit),
                )
            elif dossier_id:
                cur.execute(
                    "SELECT * FROM governance_log WHERE dossier_id = %s ORDER BY timestamp_ns DESC LIMIT %s",
                    (dossier_id, limit),
                )
            elif checkpoint:
                cur.execute(
                    "SELECT * FROM governance_log WHERE checkpoint = %s ORDER BY timestamp_ns DESC LIMIT %s",
                    (checkpoint, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM governance_log ORDER BY timestamp_ns DESC LIMIT %s",
                    (limit,),
                )
            return [dict(row) for row in cur.fetchall()]

    def query_audit_log(self, case_id: str, limit: int = 100):
        if not self._initialized:
            raise RuntimeError("Persistence not initialized")
        logs = []
        with self._connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT timestamp_ns, checkpoint, operation, scope, result, dossier_id, note "
                "FROM governance_log WHERE dossier_id = %s OR note LIKE %s "
                "ORDER BY timestamp_ns DESC LIMIT %s",
                (case_id, f"%{case_id}%", limit),
            )
            for row in cur.fetchall():
                logs.append(
                    {
                        "timestamp_ns": row[0],
                        "checkpoint": row[1],
                        "operation": row[2],
                        "scope": row[3],
                        "result": row[4],
                        "dossier_id": row[5],
                        "note": row[6],
                    }
                )
        return logs
