"""
EGREGORE LAW: SQLite Transactional Persistence Test Matrix
Expanded to match InMemoryTransactionalPersistence contract semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from egregore.domain.semantics_models import (
    AuditEvent,
    GenerateDossierCommand,
    OutboxEntry,
)
from egregore.infrastructure.persistence.sqlite_dossier_adapter import (
    SQLiteTransactionalPersistence,
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


class TestSQLiteCommitSemantics:
    def test_commit_advances_version_and_emits_zarc(self, tmp_path: Path) -> None:
        adapter = SQLiteTransactionalPersistence(
            str(tmp_path / "node.db"),
            str(tmp_path / "zarc"),
        )
        cmd = _command()
        adapter.commit_generate_t2(
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
        assert adapter.get_next_version("case-1") == 2
        assert list(
            (tmp_path / "zarc").glob("*.zarc")
        ), "expected at least one .zarc file"

    def test_idempotent_second_commit_returns_same_result(self, tmp_path: Path) -> None:
        adapter = SQLiteTransactionalPersistence(
            str(tmp_path / "node.db"),
            str(tmp_path / "zarc"),
        )
        cmd = _command()
        ack1 = adapter.commit_generate_t2(
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
        ack2 = adapter.commit_generate_t2(
            command=cmd,
            computed_data={"snapshot": "second"},  # different data, same fingerprint
            version_number=1,
            version_id="v1",
            case_next_state="active",
            events=_events(),
            outbox_entries=_outbox(),
            idempotency_fingerprint="exec_idem",
            usage_deltas=[("org_1", "dossier_generations", 1)],
            timestamp_ns=200,
        )
        # Idempotency: second call returns same result as first
        assert ack1.result.data == ack2.result.data == {"snapshot": "first"}
        assert ack1.result.version_number == ack2.result.version_number == 1
        # Version only advanced once
        assert adapter.get_next_version("case-1") == 2

    def test_version_mismatch_raises(self, tmp_path: Path) -> None:
        adapter = SQLiteTransactionalPersistence(
            str(tmp_path / "node.db"),
            str(tmp_path / "zarc"),
        )
        cmd = _command()
        adapter.commit_generate_t2(
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
        # Try to commit version 1 again (should fail, next is 2)
        with pytest.raises(RuntimeError, match="version mismatch"):
            adapter.commit_generate_t2(
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

    def test_case_isolation(self, tmp_path: Path) -> None:
        adapter = SQLiteTransactionalPersistence(
            str(tmp_path / "node.db"),
            str(tmp_path / "zarc"),
        )
        cmd_a = _command(case_id="case-a", fingerprint="fp_a")
        cmd_b = _command(case_id="case-b", fingerprint="fp_b")

        adapter.commit_generate_t2(
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
        adapter.commit_generate_t2(
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

        assert adapter.get_next_version("case-a") == 2
        assert adapter.get_next_version("case-b") == 2

        hist_a = adapter.load_case_history("case-a")
        hist_b = adapter.load_case_history("case-b")
        assert len(hist_a) == 1
        assert len(hist_b) == 1
        assert hist_a[0]["result_ir"]["snapshot"] == "a"
        assert hist_b[0]["result_ir"]["snapshot"] == "b"

    def test_history_loads_multiple_versions(self, tmp_path: Path) -> None:
        adapter = SQLiteTransactionalPersistence(
            str(tmp_path / "node.db"),
            str(tmp_path / "zarc"),
        )
        for i in range(3):
            cmd = _command(fingerprint=f"fp_{i}")
            adapter.commit_generate_t2(
                command=cmd,
                computed_data={"snapshot": f"v{i}"},
                version_number=i + 1,
                version_id=f"v{i + 1}",
                case_next_state="active",
                events=_events(f"v{i + 1}"),
                outbox_entries=_outbox(f"v{i + 1}"),
                idempotency_fingerprint=f"exec_{i}",
                usage_deltas=[("org_1", "dossier_generations", 1)],
                timestamp_ns=100 + i,
            )

        history = adapter.load_case_history("case-1")
        assert len(history) == 3
        assert [h["version"] for h in history] == [1, 2, 3]
        assert [h["result_ir"]["snapshot"] for h in history] == ["v0", "v1", "v2"]

    def test_timestamp_ns_required(self, tmp_path: Path) -> None:
        adapter = SQLiteTransactionalPersistence(
            str(tmp_path / "node.db"),
            str(tmp_path / "zarc"),
        )
        cmd = _command()
        with pytest.raises(RuntimeError, match="requires deterministic timestamp_ns"):
            adapter.commit_generate_t2(
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
        # The real check is for None, but the type system says int.
        # This test documents the boundary.

    def test_empty_events_and_outbox_allowed(self, tmp_path: Path) -> None:
        adapter = SQLiteTransactionalPersistence(
            str(tmp_path / "node.db"),
            str(tmp_path / "zarc"),
        )
        cmd = _command()
        ack = adapter.commit_generate_t2(
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
        assert adapter.get_next_version("case-1") == 2
