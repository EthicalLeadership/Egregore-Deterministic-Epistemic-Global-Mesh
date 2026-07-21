"""
BLACKSTAR LAW: DI Container Test Matrix
"""

from __future__ import annotations

from pathlib import Path

from egregore.application.code_factory import CodeFactoryService
from egregore.application.container import EgregoreContainer
from egregore.application.inference_service import InferenceService
from egregore.infrastructure.block_store import BlockStore
from egregore.infrastructure.persistence.sqlite_anchor_store import SQLiteAnchorStore
from egregore.infrastructure.persistence.sqlite_dossier_adapter import (
    SQLiteTransactionalPersistence,
)
from egregore.services.anchor_orchestrator.timestamp_client import MockTimestampClient
from egregore.shared.freeze_state import FreezeController


class TestEgregoreContainer:
    def test_for_testing_creates_instances(self, tmp_path: Path):
        container = EgregoreContainer.for_testing(tmp_path)

        assert isinstance(container.block_store, BlockStore)
        assert isinstance(container.persistence, SQLiteTransactionalPersistence)
        assert isinstance(container.anchor_store, SQLiteAnchorStore)
        assert isinstance(container.timestamp_client, MockTimestampClient)
        assert isinstance(container.freeze_controller, FreezeController)
        assert isinstance(container.inference_service, InferenceService)
        assert isinstance(container.code_factory, CodeFactoryService)
        assert container.anchor_orchestrator is not None

    def test_anchor_orchestrator_wiring(self, tmp_path: Path):
        container = EgregoreContainer.for_testing(tmp_path)
        orch = container.anchor_orchestrator

        # Verify freeze controller is wired
        assert orch._freeze_controller is container.freeze_controller

        # Verify block store is wired
        assert orch._block_store is container.block_store

    def test_persistence_commit(self, tmp_path: Path):
        from egregore.domain.semantics_models import (
            AuditEvent,
            GenerateDossierCommand,
            OutboxEntry,
        )

        container = EgregoreContainer.for_testing(tmp_path)

        command = GenerateDossierCommand(
            organization_id="org_1",
            case_id="case-1",
            actor_id="actor_1",
            input_fingerprint="fp_1",
            engine_version="engine_vA",
            policy_version="policy_v1",
            input_payload={"raw": "test"},
            causality_id="cmd-1",
            request_id=None,
        )

        container.persistence.commit_generate_t2(
            command=command,
            computed_data={"snapshot": "ok"},
            version_number=1,
            version_id="v1",
            case_next_state="active",
            events=[
                AuditEvent(
                    organization_id="org_1",
                    case_id="case-1",
                    version_id="v1",
                    event_type="audit_event",
                    event_id="event_1",
                    timestamp_ns=123,
                    event_schema_version="v0",
                    event_seq=0,
                    causality_id="cmd-1",
                    payload={"k": "v"},
                )
            ],
            outbox_entries=[
                OutboxEntry(
                    organization_id="org_1",
                    case_id="case-1",
                    version_id="v1",
                    causality_id="cmd-1",
                    side_effect_type="test",
                    outbox_id="outbox_1",
                    payload={"p": 1},
                )
            ],
            idempotency_fingerprint="exec_1",
            usage_deltas=[("org_1", "dossier_generations", 1)],
            timestamp_ns=123,
        )

        assert container.persistence.get_next_version("case-1") == 2

    def test_anchor_store_roundtrip(self, tmp_path: Path):
        from egregore.domain.anchor_record import AnchorRecord

        container = EgregoreContainer.for_testing(tmp_path)

        record = AnchorRecord(
            anchor_id="anchor-1",
            tier="tsa",
            block_hash="abc123",
            notarization="token",
            public_verify=True,
            timestamp_ns=1000,
            metadata={"source": "test"},
        )

        container.anchor_store.append(record)
        fetched = container.anchor_store.get_by_block_hash("abc123")

        assert fetched is not None
        assert fetched.anchor_id == "anchor-1"
        assert fetched.public_verify is True

    def test_freeze_controller_wiring(self, tmp_path: Path):
        container = EgregoreContainer.for_testing(tmp_path)

        # Trigger freeze via orchestrator
        container.freeze_controller.fork_detected(
            reason="test freeze",
            timestamp_ns=1000,
            detection_source="test",
        )

        assert container.freeze_controller.is_frozen
        assert len(container.freeze_controller.history) == 2

    def test_block_store_append(self, tmp_path: Path):
        from egregore.domain.execution_block import ExecutionBlock

        container = EgregoreContainer.for_testing(tmp_path)

        block = ExecutionBlock(
            block_id="block-1",
            block_seq=0,
            created_at_ns=1000,
            records=(),
            merkle_root="abc",
            previous_block_hash="0" * 64,
        ).with_integrity_hash()

        container.block_store.append(block)
        blocks = container.block_store.read_all()

        assert len(blocks) == 1
        assert blocks[0].block_id == "block-1"
