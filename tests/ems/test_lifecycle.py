"""Tests for Egregore Model Service (EMS) Lifecycle."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from egregore.ems.lifecycle import EmsLifecycle
from egregore.ems.registry import EmsRegistry, ModelStatus


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
        yield str(path)


class TestLifecycleNativeBackend:
    def test_start_loads_native_backend(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("coder-ft-v2", dummy_checkpoint, backend_type="native")
        lifecycle = EmsLifecycle(tmp_registry)

        mock_backend = MagicMock()
        mock_backend.health.return_value = True

        with patch.object(lifecycle, "_load_native_backend", return_value=mock_backend):
            rec = lifecycle.start("coder-ft-v2")

        assert rec.status == ModelStatus.RUNNING
        assert lifecycle.get_backend("coder-ft-v2") is mock_backend

    def test_start_unhealthy_backend_sets_error(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("coder-ft-v2", dummy_checkpoint, backend_type="native")
        lifecycle = EmsLifecycle(tmp_registry)

        mock_backend = MagicMock()
        mock_backend.health.return_value = False

        with patch.object(lifecycle, "_load_native_backend", return_value=mock_backend):
            with pytest.raises(Exception):
                lifecycle.start("coder-ft-v2")

        rec = tmp_registry.get("coder-ft-v2")
        assert rec is not None
        assert rec.status == ModelStatus.ERROR

    def test_stop_unloads_backend(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("coder-ft-v2", dummy_checkpoint, backend_type="native")
        lifecycle = EmsLifecycle(tmp_registry)

        mock_backend = MagicMock()
        mock_backend.health.return_value = True

        with patch.object(lifecycle, "_load_native_backend", return_value=mock_backend):
            lifecycle.start("coder-ft-v2")

        lifecycle.stop("coder-ft-v2")
        assert lifecycle.get_backend("coder-ft-v2") is None
        rec = tmp_registry.get("coder-ft-v2")
        assert rec is not None
        assert rec.status == ModelStatus.STOPPED

    def test_health_healthy_backend(self, tmp_registry, dummy_checkpoint):
        tmp_registry.register("coder-ft-v2", dummy_checkpoint, backend_type="native")
        lifecycle = EmsLifecycle(tmp_registry)

        mock_backend = MagicMock()
        mock_backend.health.return_value = True

        with patch.object(lifecycle, "_load_native_backend", return_value=mock_backend):
            lifecycle.start("coder-ft-v2")

        health = lifecycle.health("coder-ft-v2")
        assert health["status"] == "HEALTHY"

    def test_health_unknown_model(self, tmp_registry):
        lifecycle = EmsLifecycle(tmp_registry)
        health = lifecycle.health("missing")
        assert health["status"] == "UNKNOWN"
