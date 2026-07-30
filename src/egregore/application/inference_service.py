"""CBI-0 governed inference orchestrator."""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from egregore.domain.inference_models import (
    ChatRequest,
    ChatResponse,
    InferenceRecord,
)
from egregore.interface.llm_ports import ILlmClient

logger = logging.getLogger(__name__)

# Model-name prefixes that route to specific backends.
ANTHROPIC_MODEL_PREFIXES = ("claude-",)
DEEPSEEK_MODEL_PREFIXES = ("deepseek-",)
LOCAL_MODEL_PREFIXES = ("kimi-", "local-")
EGREGORE_MODEL_PREFIXES = ("egregore-", "coder-", "architect-", "my-coder")


def _resolve_backend(model: str, default_backend: str = "egregore") -> str:
    """Map a model identifier to a registered backend name."""
    lower = model.lower()
    if any(lower.startswith(prefix) for prefix in ANTHROPIC_MODEL_PREFIXES):
        return "anthropic"
    if any(lower.startswith(prefix) for prefix in DEEPSEEK_MODEL_PREFIXES):
        return "deepseek"
    if any(lower.startswith(prefix) for prefix in LOCAL_MODEL_PREFIXES):
        return "local"
    if any(lower.startswith(prefix) for prefix in EGREGORE_MODEL_PREFIXES):
        return "egregore"
    return default_backend


def build_inference_service_from_env() -> InferenceService:
    """Build the multi-backend inference service from environment variables.

    This is a standalone factory so that callers (e.g. the bootstrap layer) can
    obtain an InferenceService without pulling in the full DI container and its
    optional database dependencies.
    """
    from egregore.infrastructure.anthropic_client import AnthropicClient
    from egregore.infrastructure.deepseek_client import DeepSeekClient
    from egregore.infrastructure.coder_backend import CoderBackend
    from egregore.infrastructure.local_model_client import LocalModelClient

    clients: dict[str, ILlmClient] = {}

    # Default backend is Egregore (sovereign, native inference).
    default_backend = (
        os.environ.get("EGREGORE_DEFAULT_BACKEND", "egregore").strip() or "egregore"
    )

    # ------------------------------------------------------------------
    # Egregore native backend — loads fine-tuned Coder model directly.
    # This is THE primary backend. No Ollama. No proxies.
    # ------------------------------------------------------------------
    try:
        coder_backend = CoderBackend()
        if coder_backend.health():
            clients["egregore"] = coder_backend
            logger.info("Egregore native backend loaded with Coder model")
        else:
            logger.warning("CoderBackend health check failed")
    except Exception as exc:
        logger.warning("Egregore native backend unavailable: %s", exc)

    # Local HuggingFace-format models (e.g. Kimi K2 on the USB SSD).
    local_models_dir = os.environ.get("EGREGORE_LOCAL_MODELS_DIR", "")
    local_client = (
        LocalModelClient(models_dir=local_models_dir)
        if local_models_dir
        else LocalModelClient()
    )
    if local_client.health():
        clients["local"] = local_client

    # Anthropic Claude backend when an API key is present.
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_api_key:
        clients["anthropic"] = AnthropicClient(api_key=anthropic_api_key)

    # DeepSeek backend when an API key is present.
    deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if deepseek_api_key:
        clients["deepseek"] = DeepSeekClient(api_key=deepseek_api_key)

    return InferenceService(clients, default_backend=default_backend)


class InferenceService:
    """
    Execute LLM inference with CBI-0 governance and provenance.

    Every inference runs through M1-M4 checkpoints and produces
    a canonical InferenceRecord for .zarc provenance.

    The service hosts multiple backends and routes requests by model-name prefix.
    """

    def __init__(
        self,
        clients: dict[str, ILlmClient],
        default_backend: str = "egregore",
        pulse: Any | None = None,
    ) -> None:
        self.clients = clients
        self.default_backend = default_backend
        self.pulse = pulse

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def execute(self, request: ChatRequest) -> ChatResponse:
        """Execute a governed inference request."""
        backend = _resolve_backend(request.model, self.default_backend)
        client = self.clients.get(backend)
        if client is None:
            raise RuntimeError(
                f"Backend '{backend}' is not registered. "
                f"Available backends: {list(self.clients)}"
            )

        # M1: Projection access check
        m1_passed = self._m1_check(request)
        # M2: Registry completeness
        m2_passed = self._m2_check(request, client)
        # M3: Non-reentry
        m3_passed = self._m3_check(request)
        # M4: Spec equivalence
        m4_passed = self._m4_check(request)

        # Run inference
        response = client.chat(request)

        # Tag governance results
        governed_response = ChatResponse(
            message=response.message,
            model=response.model,
            created_at_ns=response.created_at_ns,
            usage=response.usage,
            finish_reason=response.finish_reason,
            m1_passed=m1_passed,
            m2_passed=m2_passed,
            m3_passed=m3_passed,
            m4_passed=m4_passed,
        )

        # Publish pulse metric if available
        node_id = os.environ.get("HOSTNAME", "unknown")
        if self.pulse:
            with contextlib.suppress(Exception):
                self.pulse.publish(
                    node_id=node_id,
                    metric="inference.completed",
                    value=1,
                    tags={
                        "model": request.model,
                        "mode": request.mode.value,
                        "m1": str(m1_passed),
                        "m2": str(m2_passed),
                        "m3": str(m3_passed),
                        "m4": str(m4_passed),
                    },
                )

        return governed_response

    def health(self) -> dict[str, Any]:
        """Check inference pipeline health for all registered backends."""
        backends: dict[str, dict[str, Any]] = {}
        for name, client in self.clients.items():
            try:
                reachable = client.health()
                models_available = len(client.list_models())
            except Exception:
                reachable = False
                models_available = 0
            backends[name] = {
                "reachable": reachable,
                "models_available": models_available,
            }
        return {
            "default_backend": self.default_backend,
            "backends": backends,
        }

    def list_models(self) -> Sequence[dict[str, Any]]:
        """Aggregate model lists from all registered backends."""
        models: list[dict[str, Any]] = []
        for name, client in self.clients.items():
            try:
                client_models = client.list_models()
            except Exception as exc:
                logger.warning("Backend '%s' list_models failed: %s", name, exc)
                continue
            for m in client_models:
                entry = dict(m) if isinstance(m, Mapping) else {"name": str(m)}
                entry["backend"] = name
                models.append(entry)
        return models

    def model_exists(self, name: str) -> bool:
        """Check whether a model identifier exists on any registered backend."""
        for client in self.clients.values():
            try:
                if client.model_exists(name):
                    return True
            except Exception as exc:
                logger.warning("Backend model_exists failed: %s", exc)
                continue
        return False

    def pull_model(self, name: str) -> None:
        """Route pull to the backend responsible for the model identifier."""
        backend = _resolve_backend(name, self.default_backend)
        client = self.clients.get(backend)
        if client is None:
            raise RuntimeError(f"Backend '{backend}' is not registered")
        client.pull_model(name)

    def delete_model(self, name: str) -> None:
        """Route delete to the backend responsible for the model identifier."""
        backend = _resolve_backend(name, self.default_backend)
        client = self.clients.get(backend)
        if client is None:
            raise RuntimeError(f"Backend '{backend}' is not registered")
        client.delete_model(name)

    # ------------------------------------------------------------------
    # CBI-0 governance checks (stubs — replace with real implementations)
    # ------------------------------------------------------------------
    def _m1_check(self, request: ChatRequest) -> bool:
        return True

    def _m2_check(self, request: ChatRequest, client: ILlmClient) -> bool:
        return True

    def _m3_check(self, request: ChatRequest) -> bool:
        return True

    def _m4_check(self, request: ChatRequest) -> bool:
        return True
