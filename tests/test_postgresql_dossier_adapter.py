"""
BLACKSTAR LAW: PostgreSQL Transactional Persistence Test Matrix
Same contract as SQLite, verified against live temporary PostgreSQL.
"""

from __future__ import annotations

from pathlib import Path

import pytest

testing_postgresql = pytest.importorskip("testing.postgresql")

from egregore.domain.semantics_models import (
    AuditEvent,
    GenerateDossierCommand,
    OutboxEntry,
)
from egregore.infrastructure.persistence.postgresql_dossier_adapter import (
    PostgreSQLTransactionalPersistence,
)


def _command(
    case_id: str = "case-1", fingerprint: str = "fp_1"
) -> GenerateDossierCommand:
    return GenerateDossierCommand(
        organization_id="org_1",
        case_id=case_id,
        actor_id="actor_1",
        input_fingerprint=fingerprint,
        engine_version="engine_vA",
        policy_version="policy_v1",
        input_payload={"raw": "messy legal notes"},
        causality_id="cmd-1",
        request_id=None,
    )


def _events(version_id: str = "v1") -> list[AuditEvent]:
    return [
        AuditEvent(
            organization_id="org_1",
            case_id="case-1",
            version_id=version_id,
            event_type="audit_event",
            event_id="event_1",
            timestamp_ns=123,
            event_schema_version="v0",
            event_seq=0,
            causality_id="cmd-1",
            payload={"k": "v"},
        )
    ]


def _outbox(version_id: str = "v1") -> list[OutboxEntry]:
    return [
        OutboxEntry(
            organization_id="org_1",
            case_id="case-1",
            version_id=version_id,
            causality_id="cmd-1",
            side_effect_type="test_side_effect",
            outbox_id="outbox_1",
            payload={"p": 1},
        )
    ]


@pytest.fixture(scope="function")
def pg_adapter(tmp_path: Path):
    with testing_postgresql.Postgresql() as postgresql:
        adapter = PostgreSQLTransactionalPersistence(
            dsn=postgresql.url(),
            zarc_dir=str(tmp_path / "zarc"),
        )
        yield adapter


class TestPostgreSQLCommitSemantics:
    def test_commit_advances_version_and_emits_zarc(
        self, pg_adapter: PostgreSQLTransactionalPersistence, tmp_path: Path
    ) -> None:
        cmd = _command()
        pg_adapter.commit_generate_t2(
            command=cmd,
            computed_data={"snapshot": "ok"},
            version_number=1,
            version_id="v1",
            case_next_state="active",
            events=_events(),
            outbox_entries=_outbox(),
            idempotency_fingerprint="exec_1",
            usage_deltas=[("org_1", "dossier_generations", 1)],
            timestamp_ns=123,
        )
        assert pg_adapter.get_next_version("case-1") == 2
        assert list(
            (tmp_path / "zarc").glob("*.zarc")
        ), "expected at least one .zarc file"

    def test_idempotent_second_commit_returns_same_result(
        self, pg_adapter: PostgreSQLTransactionalPersistence
    ) -> None:
        cmd = _command()
        ack1 = pg_adapter.commit_generate_t2(
            command=cmd,
            computed_data={"snapshot": "first"},
            version_number=1,
            version_id="v1",
            case_next_state="active",
            events=_events(),
            outbox_entries=_outbox(),
            idempotency_fingerprint="exec_idem",
            usage_deltas=[("org_1", "dossier_generations", 1)],
            timestamp_ns=100,
        )
        ack2 = pg_adapter.commit_generate_t2(
            command=cmd,
            computed_data={"snapshot": "second"},
            version_number=1,
            version_id="v1",
            case_next_state="active",
            events=_events(),
            outbox_entries=_outbox(),
            idempotency_fingerprint="exec_idem",
            usage_deltas=[("org_1", "dossier_generations", 1)],
            timestamp_ns=200,
        )
        assert ack1.result.data == ack2.result.data == {"snapshot": "first"}
        assert ack1.result.version_number == ack2.result.version_number == 1
        assert pg_adapter.get_next_version("case-1") == 2

    def test_version_mismatch_raises(
        self, pg_adapter: PostgreSQLTransactionalPersistence
    ) -> None:
        cmd = _command()
        pg_adapter.commit_generate_t2(
            command=cmd,
            computed_data={"snapshot": "ok"},
            version_number=1,
            version_id="v1",
            case_next_state="active",
            events=_events(),
            outbox_entries=_outbox(),
            idempotency_fingerprint="exec_1",
            usage_deltas=[("org_1", "dossier_generations", 1)],
            timestamp_ns=100,
        )
        with pytest.raises(RuntimeError, match="version mismatch"):
            pg_adapter.commit_generate_t2(
                command=cmd,
                computed_data={"snapshot": "ok"},
                version_number=1,
                version_id="v2",
                case_next_state="active",
                events=_events("v2"),
                outbox_entries=_outbox("v2"),
                idempotency_fingerprint="exec_2",
                usage_deltas=[("org_1", "dossier_generations", 1)],
                timestamp_ns=200,
            )

    def test_case_isolation(
        self, pg_adapter: PostgreSQLTransactionalPersistence
    ) -> None:
        cmd_a = _command(case_id="case-a", fingerprint="fp_a")
        cmd_b = _command(case_id="case-b", fingerprint="fp_b")

        pg_adapter.commit_generate_t2(
            command=cmd_a,
            computed_data={"snapshot": "a"},
            version_number=1,
            version_id="v1-a",
            case_next_state="active",
            events=_events(),
            outbox_entries=_outbox(),
            idempotency_fingerprint="exec_a",
            usage_deltas=[("org_1", "dossier_generations", 1)],
            timestamp_ns=100,
        )
        pg_adapter.commit_generate_t2(
            command=cmd_b,
            computed_data={"snapshot": "b"},
            version_number=1,
            version_id="v1-b",
            case_next_state="active",
            events=_events(),
            outbox_entries=_outbox(),
            idempotency_fingerprint="exec_b",
            usage_deltas=[("org_1", "dossier_generations", 1)],
            timestamp_ns=200,
        )

        assert pg_adapter.get_next_version("case-a") == 2
        assert pg_adapter.get_next_version("case-b") == 2

        hist_a = pg_adapter.load_case_history("case-a")
        hist_b = pg_adapter.load_case_history("case-b")
        assert len(hist_a) == 1
        assert len(hist_b) == 1
        assert hist_a[0]["result_ir"]["snapshot"] == "a"
        assert hist_b[0]["result_ir"]["snapshot"] == "b"

    def test_history_loads_multiple_versions(
        self, pg_adapter: PostgreSQLTransactionalPersistence
    ) -> None:
        for i in range(3):
            cmd = _command(fingerprint=f"fp_{i}")
            pg_adapter.commit_generate_t2(
                command=cmd,
                computed_data={"snapshot": f"v{i}"},
                version_number=i + 1,
                version_id=f"v{i+1}",
                case_next_state="active",
                events=_events(f"v{i+1}"),
                outbox_entries=_outbox(f"v{i+1}"),
                idempotency_fingerprint=f"exec_{i}",
                usage_deltas=[("org_1", "dossier_generations", 1)],
                timestamp_ns=100 + i,
            )

        history = pg_adapter.load_case_history("case-1")
        assert len(history) == 3
        assert [h["version"] for h in history] == [1, 2, 3]
        assert [h["result_ir"]["snapshot"] for h in history] == ["v0", "v1", "v2"]

    def test_timestamp_ns_required(
        self, pg_adapter: PostgreSQLTransactionalPersistence
    ) -> None:
        cmd = _command()
        with pytest.raises(RuntimeError, match="requires deterministic timestamp_ns"):
            pg_adapter.commit_generate_t2(
                command=cmd,
                computed_data={"snapshot": "ok"},
                version_number=1,
                version_id="v1",
                case_next_state="active",
                events=_events(),
                outbox_entries=_outbox(),
                idempotency_fingerprint="exec_1",
                usage_deltas=[("org_1", "dossier_generations", 1)],
                timestamp_ns=None,
            )

    def test_empty_events_and_outbox_allowed(
        self, pg_adapter: PostgreSQLTransactionalPersistence
    ) -> None:
        cmd = _command()
        ack = pg_adapter.commit_generate_t2(
            command=cmd,
            computed_data={"snapshot": "ok"},
            version_number=1,
            version_id="v1",
            case_next_state="active",
            events=[],
            outbox_entries=[],
            idempotency_fingerprint="exec_empty",
            usage_deltas=[],
            timestamp_ns=100,
        )
        assert ack.result.version_number == 1
        assert ack.outbox_ids == []
        assert pg_adapter.get_next_version("case-1") == 2
