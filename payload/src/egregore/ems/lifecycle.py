"""EMS Lifecycle — manager for in-process Egregore model backends.

Manages the lifecycle of native inference backends:
  start(model_id)  → load the model into the Egregore process
  stop(model_id)   → unload and free resources
  restart(model_id)→ atomic stop + start
  health(model_id) → check backend readiness
"""

from __future__ import annotations

import gc
import logging
import os
from pathlib import Path
from typing import Any

from egregore.ems.registry import EmsRegistry, ModelRecord, ModelStatus

logger = logging.getLogger(__name__)

DEFAULT_HEALTH_TIMEOUT = float(os.environ.get("EGREGORE_EMS_HEALTH_TIMEOUT", "300.0"))


class EmsLifecycleError(RuntimeError):
    """Raised when lifecycle operations fail."""


class EmsLifecycle:
    """Supervisor for Egregore-native model backends."""

    def __init__(
        self,
        registry: EmsRegistry,
        *,
        health_timeout: float = DEFAULT_HEALTH_TIMEOUT,
    ) -> None:
        self.registry = registry
        self.health_timeout = health_timeout
        self._backends: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Start / Stop / Restart
    # ------------------------------------------------------------------
    def start(self, model_id: str) -> ModelRecord:
        """Load the model into the Egregore process."""
        rec = self.registry.get(model_id)
        if rec is None:
            raise EmsLifecycleError(f"Model '{model_id}' not registered")

        path = Path(rec.model_path)
        if not path.exists():
            raise EmsLifecycleError(f"Model path missing: {path}")

        # Already running?
        backend = self._backends.get(model_id)
        if backend is not None and backend.health():
            self.registry.update_status(model_id, ModelStatus.RUNNING)
            return rec

        self.registry.update_status(model_id, ModelStatus.LOADING)

        try:
            if rec.backend_type == "native" or model_id.startswith("coder-ft-"):
                backend = self._load_native_backend(str(path))
            else:
                raise EmsLifecycleError(
                    f"Unsupported backend type: {rec.backend_type}"
                )
        except Exception as exc:
            self.registry.update_status(model_id, ModelStatus.ERROR)
            raise EmsLifecycleError(f"Failed to load model '{model_id}': {exc}") from exc

        if not backend.health():
            self.registry.update_status(model_id, ModelStatus.ERROR)
            raise EmsLifecycleError(f"Model '{model_id}' backend is not healthy")

        self._backends[model_id] = backend
        self.registry.update_status(model_id, ModelStatus.RUNNING)
        updated = self.registry.get(model_id)
        assert updated is not None
        return updated

    def _load_native_backend(self, model_path: str) -> Any:
        """Load an in-process Egregore native backend."""
        from egregore.infrastructure.coder_backend import CoderBackend

        # Ensure the native backend is enabled and points at the registered path.
        os.environ["EGREGORE_CODER_BACKEND_ENABLED"] = "true"
        os.environ["EGREGORE_CODER_MODEL_PATH"] = model_path
        return CoderBackend(model_path=model_path, enabled=True)

    def stop(self, model_id: str) -> None:
        """Unload a model and free its resources."""
        backend = self._backends.pop(model_id, None)
        if backend is not None:
            try:
                del backend
            except Exception as exc:
                logger.warning("Error unloading backend for %s: %s", model_id, exc)
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            gc.collect()
        self.registry.update_status(model_id, ModelStatus.STOPPED)
        self.registry.update_endpoint(model_id, "127.0.0.1", 0)

    def restart(self, model_id: str) -> ModelRecord:
        """Atomic stop + start."""
        self.stop(model_id)
        return self.start(model_id)

    def stop_all(self) -> None:
        """Stop every managed backend."""
        for model_id in list(self._backends.keys()):
            self.stop(model_id)

    # ------------------------------------------------------------------
    # Backend access
    # ------------------------------------------------------------------
    def get_backend(self, model_id: str) -> Any:
        """Return the loaded backend for a model, or None."""
        backend = self._backends.get(model_id)
        if backend is not None and backend.health():
            return backend
        return None

    # ------------------------------------------------------------------
    # Health checks
    # ------------------------------------------------------------------
    def health(self, model_id: str) -> dict[str, Any]:
        """Check the health of a loaded model backend."""
        rec = self.registry.get(model_id)
        if rec is None:
            return {"status": "UNKNOWN", "model_id": model_id}
        if rec.status != ModelStatus.RUNNING:
            return {"status": rec.status.value, "model_id": model_id}

        backend = self._backends.get(model_id)
        if backend is None:
            return {"status": "UNLOADED", "model_id": model_id}
        try:
            healthy = backend.health()
            return {
                "status": "HEALTHY" if healthy else "DEGRADED",
                "model_id": model_id,
            }
        except Exception as exc:
            return {
                "status": "UNREACHABLE",
                "model_id": model_id,
                "error": str(exc),
            }

    def health_all(self) -> dict[str, dict[str, Any]]:
        """Health check every registered model."""
        return {rec.model_id: self.health(rec.model_id) for rec in self.registry.list_models()}
