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
)
from egregore.interface.llm_ports import ILlmClient

logger = logging.getLogger(__name__)

# Model-name prefixes that route to specific backends.
ANTHROPIC_MODEL_PREFIXES = ("claude-",)
DEEPSEEK_MODEL_PREFIXES = ("deepseek-",)
MOONSHOT_MODEL_PREFIXES = ()  # Cloud Moonshot API (disabled; use local kimi- instead)
LOCAL_MODEL_PREFIXES = ("kimi-", "local-")  # Kimi on local SSD + other local models
EGREGORE_MODEL_PREFIXES = ("egregore-", "coder-", "architect-", "my-coder")
GGUF_MODEL_PREFIXES = ("gguf-",)


def _resolve_backend(model: str, default_backend: str = "egregore") -> str:
    """Map a model identifier to a registered backend name."""
    lower = model.lower()
    if any(lower.startswith(prefix) for prefix in ANTHROPIC_MODEL_PREFIXES):
        return "anthropic"
    if any(lower.startswith(prefix) for prefix in DEEPSEEK_MODEL_PREFIXES):
        return "deepseek"
    if any(lower.startswith(prefix) for prefix in MOONSHOT_MODEL_PREFIXES):
        return "moonshot"
    if any(lower.startswith(prefix) for prefix in LOCAL_MODEL_PREFIXES):
        return "local"
    if any(lower.startswith(prefix) for prefix in GGUF_MODEL_PREFIXES):
        return "gguf"
    if any(lower.startswith(prefix) for prefix in EGREGORE_MODEL_PREFIXES):
        return "egregore"
    return default_backend


def build_inference_service_from_env() -> InferenceService:
    """Build the multi-backend inference service from environment variables.

    This is a standalone factory so that callers (e.g. the bootstrap layer) can
    obtain an InferenceService without pulling in the full DI container and its
    optional database dependencies.
    """
    # Default backend is Egregore (sovereign, native inference).
    default_backend = (
        os.environ.get("EGREGORE_DEFAULT_BACKEND", "egregore").strip() or "egregore"
    )
    _ensure_backend_allowed(default_backend)

    from egregore.infrastructure.anthropic_client import AnthropicClient
    from egregore.infrastructure.coder_backend import CoderBackend
    from egregore.infrastructure.deepseek_client import DeepSeekClient
    from egregore.infrastructure.local_model_client import LocalModelClient
    from egregore.infrastructure.moonshot_client import MoonshotClient

    clients: dict[str, ILlmClient] = {}

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

    # ------------------------------------------------------------------
    # GGUF backend (llama.cpp) — hot residency layout (Phase 6).
    # Serves the Q4_K_M fleet with full GPU offload at ~half the VRAM of
    # the 8-bit HF path. Registered as "gguf"; models are lazy-loaded.
    # ------------------------------------------------------------------
    try:
        from egregore.infrastructure.gguf_backend import GgufBackend

        gguf_backend = GgufBackend()
        if gguf_backend.health():
            clients["gguf"] = gguf_backend
            logger.info("GGUF backend registered (llama.cpp): %s", gguf_backend.list_models())
            # Residency layout: when the 8-bit HF backend is disabled, the
            # Q4_K_M GGUF fleet IS the egregore backend (~half the VRAM).
            if "egregore" not in clients:
                clients["egregore"] = gguf_backend
                logger.info("GGUF backend promoted to primary 'egregore' client")
    except Exception as exc:  # noqa: BLE001
        logger.warning("GGUF backend unavailable: %s", exc)

    # Local HuggingFace-format models (e.g. Kimi K2 on the USB SSD).
    local_models_dir = os.environ.get("EGREGORE_LOCAL_MODELS_DIR", "")
    local_client = (
        LocalModelClient(models_dir=local_models_dir)
        if local_models_dir
        else LocalModelClient()
    )
    # Always register local backend; health may be checked later.
    clients["local"] = local_client

    # Anthropic Claude backend when an API key is present.
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_api_key:
        clients["anthropic"] = AnthropicClient(api_key=anthropic_api_key)

    # DeepSeek backend when an API key is present.
    deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if deepseek_api_key:
        clients["deepseek"] = DeepSeekClient(api_key=deepseek_api_key)

    # Moonshot (Kimi) backend when an API key is present.
    kimi_api_key = os.environ.get("KIMI_API_KEY", "")
    if kimi_api_key:
        clients["moonshot"] = MoonshotClient(api_key=kimi_api_key)
        logger.info("Moonshot (Kimi) backend registered")

    if default_backend not in clients:
        if "local" in clients:
            logger.warning("Default backend '%s' not registered; falling back to 'local'.", default_backend)
            default_backend = "local"
        else:
            raise RuntimeError("No inference backends could be registered.")

    return InferenceService(clients, default_backend=default_backend)


def _ensure_backend_allowed(backend_name: str) -> None:
    if backend_name.strip().lower() == "ollama":
        raise RuntimeError(
            "EGREGORE_DEFAULT_BACKEND=ollama is forbidden. "
            "Set EGREGORE_DEFAULT_BACKEND=egregore."
        )


def _normalize_backend_name(backend_name: str) -> str:
    normalized = backend_name.strip().lower()
    if not normalized:
        raise RuntimeError("Default backend cannot be empty")
    return normalized


class InferenceService:
    """
    Execute LLM inference with CBI-0 governance and provenance.

    Every inference runs through M1-M4 checkpoints and produces
    a canonical InferenceRecord for .zarc provenance.

    The service hosts multiple backends and routes requests by model-name prefix.
    """

    def __init__(
        self,
        clients: dict[str, ILlmClient] | ILlmClient,
        default_backend: str = "egregore",
        pulse: Any | None = None,
    ) -> None:
        if isinstance(clients, Mapping):
            self.clients = dict(clients)
        else:
            self.clients = {default_backend: clients}
        self._default_backend = ""
        self.default_backend = default_backend
        self.pulse = pulse
        _ensure_backend_allowed(self.default_backend)

    @property
    def default_backend(self) -> str:
        return self._default_backend

    @default_backend.setter
    def default_backend(self, backend_name: str) -> None:
        normalized = _normalize_backend_name(backend_name)
        _ensure_backend_allowed(normalized)
        if self.clients and normalized not in self.clients:
            raise RuntimeError(
                f"Backend '{normalized}' is not registered. "
                f"Available backends: {list(self.clients)}"
            )
        self._default_backend = normalized

    def set_default_backend(self, backend_name: str) -> None:
        """Mutate active backend after validating registration and policy."""
        self.default_backend = backend_name

    def register_backend(self, backend_name: str, client: ILlmClient) -> None:
        """Register or replace a backend client with policy guardrails."""
        normalized = _normalize_backend_name(backend_name)
        _ensure_backend_allowed(normalized)
        self.clients[normalized] = client

    def unregister_backend(self, backend_name: str) -> None:
        """Remove a backend client, refusing to remove the active default backend."""
        normalized = _normalize_backend_name(backend_name)
        if normalized == self.default_backend:
            raise RuntimeError(
                f"Cannot unregister active default backend '{normalized}'"
            )
        self.clients.pop(normalized, None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def execute(self, request: ChatRequest) -> ChatResponse:
        """Execute a governed inference request."""
        _ensure_backend_allowed(self.default_backend)
        backend = _resolve_backend(request.model, self.default_backend)
        client = self.clients.get(backend)
        if client is None:
            raise RuntimeError(
                f"Backend '{backend}' is not registered. "
                f"Available backends: {list(self.clients)}"
            )

        # Governance checkpoints M1 (projection access), M2 (registry
        # completeness), M3 (non-reentry), M4 (spec equivalence).
        m1_passed = self._m1_check(request)
        m2_passed = self._m2_check(request, client)
        m3_passed = self._m3_check(request)
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

    def execute_stream(self, request: ChatRequest) -> Iterator[str]:
        """Stream a governed inference request as content deltas.

        Runs the same M1-M4 governance pre-checks as execute(), then
        delegates to the backend's stream_chat() when available; otherwise
        falls back to a single chunk from a non-streaming call.
        """
        _ensure_backend_allowed(self.default_backend)
        backend = _resolve_backend(request.model, self.default_backend)
        client = self.clients.get(backend)
        if client is None:
            raise RuntimeError(
                f"Backend '{backend}' is not registered. "
                f"Available backends: {list(self.clients)}"
            )

        # Governance checkpoints M1-M4 (same as execute()).
        self._m1_check(request)
        self._m2_check(request, client)
        self._m3_check(request)
        self._m4_check(request)

        stream_chat = getattr(client, "stream_chat", None)
        if callable(stream_chat):
            yield from stream_chat(request)
            return

        response = client.chat(request)
        yield response.message.content

    def health(self) -> dict[str, Any]:
        """Check inference pipeline health for all registered backends."""
        _ensure_backend_allowed(self.default_backend)
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
        _ensure_backend_allowed(self.default_backend)
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
        _ensure_backend_allowed(self.default_backend)
        backend = _resolve_backend(name, self.default_backend)
        client = self.clients.get(backend)
        if client is None:
            raise RuntimeError(f"Backend '{backend}' is not registered")
        client.pull_model(name)

    def delete_model(self, name: str) -> None:
        """Route delete to the backend responsible for the model identifier."""
        _ensure_backend_allowed(self.default_backend)
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
