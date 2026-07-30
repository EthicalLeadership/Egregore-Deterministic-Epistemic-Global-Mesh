"""Tests for Egregore Model Service (EMS) Registry."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from egregore.ems.registry import EmsRegistry, ModelRecord, ModelStatus


@pytest.fixture
def tmp_registry():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_ems.db"
        reg = EmsRegistry(db_path=db_path)
        yield reg


@pytest.fixture
def dummy_checkpoint():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "coder-ft-v2"
        path.mkdir()
        (path / "config.json").write_text('{"vocab_size": 32022}')
        (path / "model.safetensors").write_bytes(b"x" * 256)
        yield str(path)


class TestRegistryCrud:
    def test_register_and_get(self, tmp_registry, dummy_checkpoint):
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

    def test_register_missing_path_raises(self, tmp_registry):
        with pytest.raises(FileNotFoundError):
            tmp_registry.register("missing", "/nonexistent/model")

    def test_list_models(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("model-a", dummy_checkpoint, tier="general")
        tmp_registry.register("model-b", dummy_checkpoint, tier="expert")
        all_models = tmp_registry.list_models()
        assert len(all_models) == 2

        general = tmp_registry.list_models(tier="general")
        assert len(general) == 1
        assert general[0].model_id == "model-a"

    def test_update_status(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("m1", dummy_checkpoint)
        tmp_registry.update_status("m1", ModelStatus.RUNNING)
        rec = tmp_registry.get("m1")
        assert rec is not None
        assert rec.status == ModelStatus.RUNNING

    def test_update_endpoint(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("m1", dummy_checkpoint)
        tmp_registry.update_endpoint("m1", "192.168.1.10", 9090)
        rec = tmp_registry.get("m1")
        assert rec is not None
        assert rec.host == "192.168.1.10"
        assert rec.port == 9090

    def test_delete(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("m1", dummy_checkpoint)
        assert tmp_registry.delete("m1") is True
        assert tmp_registry.get("m1") is None
        assert tmp_registry.delete("m1") is False


class TestRegistryDiscovery:
    def test_scan_model_directory(self, tmp_registry):
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

    def test_verify_all(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("m1", dummy_checkpoint)
        results = tmp_registry.verify_all()
        assert results["m1"] == "VERIFIED"

    def test_verify_missing(self, tmp_registry, dummy_checkpoint):
        rec = tmp_registry.register("m1", dummy_checkpoint)
        # Delete the config after registration
        Path(dummy_checkpoint, "config.json").unlink()
        results = tmp_registry.verify_all()
        assert results["m1"] == "CORRUPT"
        updated = tmp_registry.get("m1")
        assert updated is not None
        assert updated.status == ModelStatus.ERROR

    def test_health(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("m1", dummy_checkpoint)
        health = tmp_registry.health()
        assert health["status"] == "HEALTHY"
        assert health["total_models"] == 1

    def test_chat_template_persistence(self, tmp_registry, dummy_checkpoint):
        rec = tmp_registry.register(
            "m1",
            dummy_checkpoint,
            chat_template="deepseek",
        )
        assert rec.chat_template == "deepseek"
        fetched = tmp_registry.get("m1")
        assert fetched is not None
        assert fetched.chat_template == "deepseek"


class TestModelRecord:
    def test_to_dict(self):
        rec = ModelRecord(
            model_id="test",
            version="v1",
            model_path="/tmp/test",
            status=ModelStatus.RUNNING,
        )
        d = rec.to_dict()
        assert d["model_id"] == "test"
        assert d["status"] == "running"
