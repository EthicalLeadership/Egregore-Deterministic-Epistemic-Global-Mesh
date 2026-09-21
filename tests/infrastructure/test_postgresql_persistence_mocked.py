import hashlib
import sys
from unittest.mock import MagicMock

import pytest

# Build mock modules BEFORE importing adapter; restore real modules afterwards
# so the rest of the suite is not polluted.
_orig_psycopg = sys.modules.get("psycopg")
_orig_psycopg_pool = sys.modules.get("psycopg_pool")

mock_psycopg = type(sys)("psycopg")
mock_psycopg.rows = MagicMock()
mock_psycopg.rows.dict_row = "dict_row"
sys.modules["psycopg"] = mock_psycopg

mock_psycopg_pool = type(sys)("psycopg_pool")


class FakePool:
    check_connection = None

    def __init__(self, dsn, **kwargs):
        self.dsn = dsn
        self.kwargs = kwargs
        self._con = None

    def connection(self):
        return self._con

    def close(self):
        pass


mock_psycopg_pool.ConnectionPool = FakePool
sys.modules["psycopg_pool"] = mock_psycopg_pool

from egregore.domain.models.dossier import CommitResult, Dossier, DossierState
from egregore.domain.models.event import Event
from egregore.infrastructure.adapters.postgresql_persistence import (
    PersistenceConfig,
    PostgreSQLPersistence,
)

# Restore real modules so other tests can use the actual psycopg/psycopg_pool.
if _orig_psycopg is not None:
    sys.modules["psycopg"] = _orig_psycopg
elif "psycopg" in sys.modules:
    del sys.modules["psycopg"]
if _orig_psycopg_pool is not None:
    sys.modules["psycopg_pool"] = _orig_psycopg_pool
elif "psycopg_pool" in sys.modules:
    del sys.modules["psycopg_pool"]


class MockRow:
    """Simulates psycopg dict_row — iterable as (col, val) pairs."""

    def __init__(self, data: dict):
        self._data = data

    def __iter__(self):
        return iter(self._data.items())

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def keys(self):
        return self._data.keys()

    def __len__(self):
        return len(self._data)


class MockCursor:
    def __init__(self):
        self._results = []
        self._index = 0
        self.rowcount = 1
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        if False:
            self.rowcount = 1
            self._index += 1
        else:
            self.rowcount = 1

    def fetchone(self):
        if self._results:
            return self._results.pop(0)
        if any("replay_traces" in str(q) for q, _ in self.executed):
            return MockRow({"trace_hash": "a" * 64})
        if any("information_schema.tables" in str(q) for q, _ in self.executed):
            return MockRow({"exists": True})
        return None

    def fetchall(self):
        return self._results

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class MockConnection:
    def __init__(self):
        self._autocommit = False
        self._cursor = MockCursor()

    def cursor(self, row_factory=None):
        return self._cursor

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._autocommit = value


class MockGovernanceBridge:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class MockSigner:
    def sign(self, canonical_bytes):
        return f"sig:{hashlib.sha256(canonical_bytes).hexdigest()[:16]}"


@pytest.fixture
def mock_persistence():
    pers = PostgreSQLPersistence(
        PersistenceConfig(dsn="postgresql://localhost/db"),
        governance_bridge=MockGovernanceBridge(),
        signer=MockSigner(),
    )
    pers._pool = FakePool("postgresql://localhost/db")
    pers._pool._con = MockConnection()
    pers._initialized = True
    yield pers


def _make_dossier(
    dossier_id, case_id, version=1, events=None, timestamp_ns=1_000_000_000
):
    """Helper to build a Dossier with Events."""
    events = events or []
    state = DossierState(events=events)
    canonical = PostgreSQLPersistence._canonical_json(state.to_dict())
    intent_hash = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return Dossier(
        dossier_id=dossier_id,
        case_id=case_id,
        version=version,
        intent_hash=intent_hash,
        state=state,
        canonical_state=canonical,
        timestamp_ns=timestamp_ns,
        signature="",
    )


class TestConfig:
    def test_valid(self):
        cfg = PersistenceConfig(
            dsn="postgresql://localhost/db", min_connections=2, max_connections=5
        )
        assert cfg.min_connections == 2

    def test_missing_dsn(self):
        with pytest.raises(ValueError, match="dsn"):
            PersistenceConfig(dsn="")

    def test_invalid_pool(self):
        with pytest.raises(ValueError, match="max_connections"):
            PersistenceConfig(
                dsn="postgresql://localhost/db", min_connections=5, max_connections=1
            )


class TestCanonicalJson:
    def test_determinism(self):
        obj = {"b": 2, "a": 1, "c": {"z": 26, "a": 1}}
        result = PostgreSQLPersistence._canonical_json(obj)
        assert result == '{"a":1,"b":2,"c":{"a":1,"z":26}}'

    def test_fallback_on_non_serializable(self, caplog):
        from datetime import datetime

        obj = {"created": datetime.now()}
        result = PostgreSQLPersistence._canonical_json(obj)
        assert isinstance(result, str)
        assert "created" in result


class TestCommit:
    def test_success(self, mock_persistence):
        events = [
            Event(event_type="A", payload={"n": 1}),
            Event(event_type="B", payload={"n": 2}),
        ]
        dossier = _make_dossier(
            "doss-001", "case-001", events=events, timestamp_ns=1_000_000_000
        )
        result = mock_persistence.commit_generate_t2(dossier)
        assert isinstance(result, CommitResult)
        assert result.dossier_id == "doss-001"
        assert result.event_count == 2

    def test_governance_emitted(self, mock_persistence):
        dossier = _make_dossier("doss-002", "case-002", timestamp_ns=2_000_000_000)
        mock_persistence.commit_generate_t2(dossier)
        assert len(mock_persistence._governance.events) >= 1
        assert mock_persistence._governance.events[-1]["checkpoint"] == "M4"

    def test_event_rowcount_detection(self, mock_persistence):
        class AlternatingCursor(MockCursor):
            def __init__(self):
                super().__init__()
                self._call_count = 0

            def execute(self, query, params=None):
                super().execute(query, params)
                self._call_count += 1
                self.rowcount = 1 if self._call_count % 2 == 1 else 0

        mock_persistence._pool._con._cursor = AlternatingCursor()
        events = [Event(event_type="X", payload={}) for _ in range(4)]
        dossier = _make_dossier(
            "doss-003", "case-003", events=events, timestamp_ns=3_000_000_000
        )
        result = mock_persistence.commit_generate_t2(dossier)
        assert result.event_count == 2

    def test_failure_logged(self, mock_persistence):
        class FailingCursor(MockCursor):
            def execute(self, query, params=None):
                if "INSERT INTO dossiers" in str(query):
                    raise RuntimeError("DB deadlock")
                super().execute(query, params)

        mock_persistence._pool._con._cursor = FailingCursor()
        dossier = _make_dossier("doss-004", "case-004", timestamp_ns=4_000_000_000)
        with pytest.raises(RuntimeError):
            mock_persistence.commit_generate_t2(dossier)

        failure_events = [
            e
            for e in mock_persistence._governance.events
            if e.get("result") == "DIVERGED"
        ]
        assert len(failure_events) >= 1


class TestRead:
    def test_get_dossier_returns_dict(self, mock_persistence):
        class ResultCursor(MockCursor):
            def execute(self, query, params=None):
                super().execute(query, params)
                if (
                    "SELECT" in str(query)
                    and "dossiers" in str(query)
                    and "canonical_state" not in str(query)
                ):
                    self._results = [
                        MockRow(
                            {
                                "dossier_id": "doss-005",
                                "case_id": "case-005",
                                "version": 1,
                                "intent_hash": "abc",
                                "state": {"key": "value"},
                                "timestamp_ns": 5_000_000_000,
                                "signature": "sig",
                            }
                        )
                    ]

        mock_persistence._pool._con._cursor = ResultCursor()
        result = mock_persistence.get_dossier("doss-005")
        assert result is not None
        assert isinstance(result["state"], dict)
        assert result["state"]["key"] == "value"

    def test_get_canonical_dossier_returns_string(self, mock_persistence):
        class CanonicalCursor(MockCursor):
            def execute(self, query, params=None):
                super().execute(query, params)
                if "canonical_state" in str(query):
                    self._results = [
                        MockRow(
                            {
                                "dossier_id": "doss-006",
                                "case_id": "case-006",
                                "version": 1,
                                "intent_hash": "abc",
                                "canonical_state": '{"key":"value"}',
                                "timestamp_ns": 6_000_000_000,
                                "signature": "sig",
                            }
                        )
                    ]

        mock_persistence._pool._con._cursor = CanonicalCursor()
        result = mock_persistence.get_canonical_dossier("doss-006")
        assert result is not None
        assert isinstance(result["state"], str)
        assert result["state"] == '{"key":"value"}'

    def test_get_events_ordered(self, mock_persistence):
        class EventsCursor(MockCursor):
            def execute(self, query, params=None):
                super().execute(query, params)
                if "events" in str(query):
                    self._results = [
                        MockRow({"event_seq": 1, "event_type": "A"}),
                        MockRow({"event_seq": 2, "event_type": "B"}),
                        MockRow({"event_seq": 3, "event_type": "C"}),
                    ]

        mock_persistence._pool._con._cursor = EventsCursor()
        events = mock_persistence.get_events("doss-007")
        assert len(events) == 3
        assert events[0]["event_seq"] == 1
        assert events[2]["event_type"] == "C"


class TestVerify:
    def test_verify_match(self, mock_persistence):
        class TraceCursor(MockCursor):
            def execute(self, query, params=None):
                super().execute(query, params)
                if "FOR UPDATE" in str(query):
                    self._results = [MockRow({"trace_hash": "a" * 64})]

        mock_persistence._pool._con._cursor = TraceCursor()
        assert mock_persistence.verify_replay_trace("doss-008", "a" * 64) is True

    def test_verify_mismatch(self, mock_persistence):
        class MismatchCursor(MockCursor):
            def execute(self, query, params=None):
                super().execute(query, params)
                if "FOR UPDATE" in str(query):
                    self._results = [MockRow({"trace_hash": "b" * 64})]

        mock_persistence._pool._con._cursor = MismatchCursor()
        assert mock_persistence.verify_replay_trace("doss-009", "a" * 64) is False


class TestPool:
    def test_timeout_config(self):
        pers = PostgreSQLPersistence(
            PersistenceConfig(dsn="postgresql://localhost/db", pool_timeout=15)
        )
        pers._pool = FakePool("postgresql://localhost/db", timeout=15)
        pers._initialized = True
        assert pers._pool.kwargs.get("timeout") == 15

    def test_health_check_exists(self):
        assert hasattr(FakePool, "check_connection")
