"""Tests for Egregore Model Service (EMS) Registry."""

# pyright: reportMissingParameterType=false, reportUnknownParameterType=false

from __future__ import annotations

from collections.abc import Iterator
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from egregore.ems.registry import EmsRegistry, ModelRecord, ModelStatus
from egregore.infrastructure.zarc_provenance_sink import ZarcProvenanceSink
from egregore.kernel.provenance import Provenance


class RecordingSink:
    def __init__(self, fail_on_first_append: bool = False) -> None:
        self.fail_on_first_append = fail_on_first_append
        self.events: list[str] = []

    def append(self, event: object) -> None:
        event_name = getattr(event, "event", "")
        self.events.append(str(event_name))
        if self.fail_on_first_append and len(self.events) == 1:
            raise RuntimeError("sink failure")


@pytest.fixture
def tmp_registry() -> Iterator[EmsRegistry]:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_ems.db"
        signing_key = SigningKey.generate()
        provenance = Provenance(
            zarc_path=Path(tmp) / "test_ems.zarc",
            signing_key_hex=signing_key.encode().hex(),
        )
        reg = EmsRegistry(
            db_path=db_path,
            provenance_sink=ZarcProvenanceSink(provenance=provenance),
        )
        yield reg


@pytest.fixture
def dummy_checkpoint() -> Iterator[str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "coder-ft-v2"
        path.mkdir()
        (path / "config.json").write_text('{"vocab_size": 32022}')
        (path / "model.safetensors").write_bytes(b"x" * 256)
        yield str(path)


@pytest.fixture
def approval_bundle(dummy_checkpoint: str) -> Iterator[tuple[Path, Path, Path]]:
    signing_key = SigningKey.generate()
    artifact = {
        "decision": "APPROVED",
        "approved_by": "operator@example",
        "approved_at_ns": 123456789,
        "model_family": "coder-ft-v2",
        "checkpoints_dir": dummy_checkpoint,
        "human_approval_required": True,
        "rationale": "manual review complete",
    }
    with tempfile.TemporaryDirectory() as tmp:
        artifact_path = Path(tmp) / "approval.json"
        signature_path = Path(tmp) / "approval.sig"
        public_key_path = Path(tmp) / "approval.pub"
        payload = json.dumps(artifact, separators=(",", ":")).encode("utf-8")
        artifact_path.write_bytes(payload)
        signature_path.write_text(signing_key.sign(payload).signature.hex())
        public_key_path.write_text(signing_key.verify_key.encode().hex())
        yield artifact_path, signature_path, public_key_path


class TestRegistryCrud:
    def test_register_and_get(self, tmp_registry: EmsRegistry, dummy_checkpoint: str) -> None:
        rec = tmp_registry.register(
            model_id="coder-ft-v2",
            model_path=dummy_checkpoint,
            version="v1",
            tier="expert",
        )
        assert rec.model_id == "coder-ft-v2"
        assert rec.status == ModelStatus.STOPPED

        fetched = tmp_registry.get("coder-ft-v2")
        assert fetched is not None
        assert fetched.version == "v1"
        assert fetched.tier == "expert"
        assert fetched.sha256 != ""

    def test_register_missing_path_raises(self, tmp_registry: EmsRegistry) -> None:
        with pytest.raises(FileNotFoundError):
            tmp_registry.register("missing", "/nonexistent/model")

    def test_list_models(self, tmp_registry: EmsRegistry, dummy_checkpoint: str) -> None:
        tmp_registry.register("model-a", dummy_checkpoint, tier="general")
        tmp_registry.register("model-b", dummy_checkpoint, tier="expert")
        all_models = tmp_registry.list_models()
        assert len(all_models) == 2

        general = tmp_registry.list_models(tier="general")
        assert len(general) == 1
        assert general[0].model_id == "model-a"

    def test_update_status(self, tmp_registry: EmsRegistry, dummy_checkpoint: str) -> None:
        tmp_registry.register("m1", dummy_checkpoint)
        tmp_registry.update_status("m1", ModelStatus.RUNNING)
        rec = tmp_registry.get("m1")
        assert rec is not None
        assert rec.status == ModelStatus.RUNNING

    def test_update_endpoint(self, tmp_registry: EmsRegistry, dummy_checkpoint: str) -> None:
        tmp_registry.register("m1", dummy_checkpoint)
        tmp_registry.update_endpoint("m1", "192.168.1.10", 9090)
        rec = tmp_registry.get("m1")
        assert rec is not None
        assert rec.host == "192.168.1.10"
        assert rec.port == 9090

    def test_delete(self, tmp_registry: EmsRegistry, dummy_checkpoint: str) -> None:
        tmp_registry.register("m1", dummy_checkpoint)
        assert tmp_registry.delete("m1") is True
        assert tmp_registry.get("m1") is None
        assert tmp_registry.delete("m1") is False


class TestRegistryDiscovery:
    def test_scan_model_directory(self, tmp_registry: EmsRegistry) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "expert"
            root.mkdir(parents=True)
            (root / "model-a").mkdir()
            (root / "model-a" / "config.json").write_text("{}")
            (root / "model-b").mkdir()
            (root / "model-b" / "config.json").write_text("{}")

            registered = tmp_registry.scan_model_directory(root, tier="expert")
            assert len(registered) == 2
            ids = {r.model_id for r in registered}
            assert ids == {"model-a", "model-b"}

    def test_verify_all(self, tmp_registry: EmsRegistry, dummy_checkpoint: str) -> None:
        tmp_registry.register("m1", dummy_checkpoint)
        results = tmp_registry.verify_all()
        assert results["m1"] == "VERIFIED"

    def test_verify_missing(self, tmp_registry: EmsRegistry, dummy_checkpoint: str) -> None:
        rec = tmp_registry.register("m1", dummy_checkpoint)
        # Delete the config after registration
        Path(dummy_checkpoint, "config.json").unlink()
        results = tmp_registry.verify_all()
        assert results["m1"] == "CORRUPT"
        updated = tmp_registry.get("m1")
        assert updated is not None
        assert updated.status == ModelStatus.ERROR

    def test_health(self, tmp_registry: EmsRegistry, dummy_checkpoint: str) -> None:
        tmp_registry.register("m1", dummy_checkpoint)
        health = tmp_registry.health()
        assert health["status"] == "HEALTHY"
        assert health["total_models"] == 1

    def test_chat_template_persistence(self, tmp_registry: EmsRegistry, dummy_checkpoint: str) -> None:
        rec = tmp_registry.register(
            "m1",
            dummy_checkpoint,
            chat_template="deepseek",
        )
        assert rec.chat_template == "deepseek"
        fetched = tmp_registry.get("m1")
        assert fetched is not None
        assert fetched.chat_template == "deepseek"

    def test_promote_records_signed_approval(
        self,
        tmp_registry: EmsRegistry,
        dummy_checkpoint: str,
        approval_bundle: tuple[Path, Path, Path],
    ) -> None:
        artifact_path, signature_path, public_key_path = approval_bundle
        approval = tmp_registry.promote(
            model_id="coder-ft-v2",
            checkpoints_dir=dummy_checkpoint,
            approval_artifact=str(artifact_path),
            approval_signature=str(signature_path),
            approval_public_key=str(public_key_path),
        )

        assert approval["model_id"] == "coder-ft-v2"
        assert approval["approved_by"] == "operator@example"
        zarc_path = tmp_registry.db_path.parent / "test_ems.zarc"
        assert zarc_path.exists()
        zarc_text = zarc_path.read_text(encoding="utf-8")
        assert "model_promotion_approved" in zarc_text

    def test_promote_blocks_without_approval_artifact(
        self,
        tmp_registry: EmsRegistry,
        dummy_checkpoint: str,
        approval_bundle: tuple[Path, Path, Path],
    ) -> None:
        artifact_path, signature_path, public_key_path = approval_bundle
        artifact_path.unlink()

        with pytest.raises(FileNotFoundError):
            tmp_registry.promote(
                model_id="coder-ft-v2",
                checkpoints_dir=dummy_checkpoint,
                approval_artifact=str(artifact_path),
                approval_signature=str(signature_path),
                approval_public_key=str(public_key_path),
            )

    def test_promote_blocks_on_invalid_signature(
        self,
        tmp_registry: EmsRegistry,
        dummy_checkpoint: str,
        approval_bundle: tuple[Path, Path, Path],
    ) -> None:
        artifact_path, signature_path, public_key_path = approval_bundle
        signature_path.write_text("00")

        with pytest.raises(Exception, match="Approval signature verification failed|The signature must be exactly 64 bytes long"):
            tmp_registry.promote(
                model_id="coder-ft-v2",
                checkpoints_dir=dummy_checkpoint,
                approval_artifact=str(artifact_path),
                approval_signature=str(signature_path),
                approval_public_key=str(public_key_path),
            )

    def test_promote_blocks_without_provenance_sink(
        self,
        dummy_checkpoint: str,
        approval_bundle: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        artifact_path, signature_path, public_key_path = approval_bundle
        monkeypatch.delenv("EGREGORE_ZARC_SIGNING_KEY_HEX", raising=False)
        monkeypatch.delenv("EGREGORE_EMS_ZARC_PATH", raising=False)
        with tempfile.TemporaryDirectory() as tmp:
            reg = EmsRegistry(db_path=Path(tmp) / "test_ems.db")
            with pytest.raises(RuntimeError, match="mutable-only audit trail"):
                reg.promote(
                    model_id="coder-ft-v2",
                    checkpoints_dir=dummy_checkpoint,
                    approval_artifact=str(artifact_path),
                    approval_signature=str(signature_path),
                    approval_public_key=str(public_key_path),
                )

    def test_promote_rolls_back_if_provenance_emit_fails(
        self,
        dummy_checkpoint: str,
        approval_bundle: tuple[Path, Path, Path],
    ) -> None:
        artifact_path, signature_path, public_key_path = approval_bundle
        with tempfile.TemporaryDirectory() as tmp:
            sink = RecordingSink(fail_on_first_append=True)
            reg = EmsRegistry(db_path=Path(tmp) / "test_ems.db", provenance_sink=sink)
            with pytest.raises(RuntimeError, match="sink failure"):
                reg.promote(
                    model_id="coder-ft-v2",
                    checkpoints_dir=dummy_checkpoint,
                    approval_artifact=str(artifact_path),
                    approval_signature=str(signature_path),
                    approval_public_key=str(public_key_path),
                )
            row = reg._conn.execute("SELECT COUNT(*) AS c FROM promotions").fetchone()
            assert row is not None
            assert int(row["c"]) == 0

    def test_promote_emits_compensation_if_commit_fails_after_provenance(
        self,
        dummy_checkpoint: str,
        approval_bundle: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        artifact_path, signature_path, public_key_path = approval_bundle
        with tempfile.TemporaryDirectory() as tmp:
            sink = RecordingSink()
            reg = EmsRegistry(db_path=Path(tmp) / "test_ems.db", provenance_sink=sink)

            def _boom() -> None:
                raise sqlite3.OperationalError("commit failed")

            monkeypatch.setattr(reg, "_commit_promotion_transaction", _boom)

            with pytest.raises(RuntimeError, match="compensation event emitted"):
                reg.promote(
                    model_id="coder-ft-v2",
                    checkpoints_dir=dummy_checkpoint,
                    approval_artifact=str(artifact_path),
                    approval_signature=str(signature_path),
                    approval_public_key=str(public_key_path),
                )

            row = reg._conn.execute("SELECT COUNT(*) AS c FROM promotions").fetchone()
            assert row is not None
            assert int(row["c"]) == 0
            assert sink.events == [
                "model_promotion_approved",
                "model_promotion_reverted",
            ]


class TestModelRecord:
    def test_to_dict(self) -> None:
        rec = ModelRecord(
            model_id="test",
            version="v1",
            model_path="/tmp/test",
            status=ModelStatus.RUNNING,
        )
        d = rec.to_dict()
        assert d["model_id"] == "test"
        assert d["status"] == "running"
