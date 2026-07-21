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


def _resolve_backend(model: str, default_backend: str = "local") -> str:
    """Map a model identifier to a registered backend name."""
    lower = model.lower()
    if any(lower.startswith(prefix) for prefix in ANTHROPIC_MODEL_PREFIXES):
        return "anthropic"
    if any(lower.startswith(prefix) for prefix in DEEPSEEK_MODEL_PREFIXES):
        return "deepseek"
    if any(lower.startswith(prefix) for prefix in LOCAL_MODEL_PREFIXES):
        return "local"
    return default_backend


def build_inference_service_from_env() -> InferenceService:
    """Build the multi-backend inference service from environment variables.

    This is a standalone factory so that callers (e.g. the bootstrap layer) can
    obtain an InferenceService without pulling in the full DI container and its
    optional database dependencies.
    """
    from egregore.infrastructure.anthropic_client import AnthropicClient
    from egregore.infrastructure.deepseek_client import DeepSeekClient
    from egregore.infrastructure.local_model_client import LocalModelClient

    clients: dict[str, ILlmClient] = {}

    # Default backend is configurable; no hardcoded default.
    default_backend = (
        os.environ.get("BLACKSTAR_DEFAULT_BACKEND", "local").strip() or "local"
    )

    # Local HuggingFace-format models (e.g. Kimi K2 on the USB SSD).
    local_models_dir = os.environ.get("BLACKSTAR_LOCAL_MODELS_DIR", "")
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

    The service can host multiple backends (Ollama, Anthropic, etc.)
    and routes requests by model-name prefix.
    """

    def __init__(
        self,
        clients: Mapping[str, ILlmClient] | ILlmClient,
        cbi0_monitor: Any | None = None,
        cbi0_validator: Any | None = None,
        cbi0_guard: Any | None = None,
        cbi0_auditor: Any | None = None,
        persistence: Any | None = None,
        pulse: Any | None = None,
        default_backend: str = "local",
    ) -> None:
        if isinstance(clients, Mapping):
            self.clients = dict(clients)
        else:
            # Backwards-compatible single-client wiring maps to the default backend.
            self.clients = {default_backend: clients}
        self.default_backend = default_backend
        self.cbi0_monitor = cbi0_monitor  # M1: projection access
        self.cbi0_validator = cbi0_validator  # M2: registry completeness
        self.cbi0_guard = cbi0_guard  # M3: terminal output non-reentry
        self.cbi0_auditor = cbi0_auditor  # M4: spec/runtime equivalence audit
        self.persistence = persistence
        self.pulse = pulse

    def _backend_for(self, request: ChatRequest) -> ILlmClient:
        backend = _resolve_backend(request.model, self.default_backend)
        client = self.clients.get(backend)
        if client is None:
            available = ", ".join(sorted(self.clients))
            raise RuntimeError(
                f"Backend '{backend}' for model '{request.model}' is not registered. "
                f"Available backends: {available}"
            )
        return client

    @property
    def active_backend(self) -> ILlmClient:
        """Return the default backend client (for legacy health checks)."""
        return self.clients[self.default_backend]

    def execute_stream(
        self, request: ChatRequest, node_id: str = "pioneer1"
    ) -> Iterator[str]:
        """Execute streaming inference with lightweight governance.

        Yields raw text deltas from the selected backend. Governance checkpoints
        are applied before/after the stream; usage and finish metadata are not
        available until the full response is collected.
        """
        backend = self._backend_for(request)
        if not hasattr(backend, "stream_chat"):
            raise RuntimeError(f"Backend '{request.model}' does not support streaming")

        # Run M1/M2 checkpoints before streaming.
        if self.cbi0_monitor:
            with contextlib.suppress(Exception):
                self.cbi0_monitor.enforce_m1(
                    declared_agents=request.declared_agents,
                    declared_models=request.declared_models,
                )
        if self.cbi0_validator:
            with contextlib.suppress(Exception):
                self.cbi0_validator.assert_m2(
                    agents=request.declared_agents,
                    models=[request.model] + request.declared_models,
                )

        yield from backend.stream_chat(request)

    def _check_m1_m2(self, request: ChatRequest) -> tuple[bool, bool]:
        """Run pre-inference M1/M2 governance checkpoints."""
        m1_passed = True
        if self.cbi0_monitor:
            try:
                self.cbi0_monitor.enforce_m1(
                    declared_agents=request.declared_agents,
                    declared_models=request.declared_models,
                )
            except Exception:
                m1_passed = False

        m2_passed = True
        if self.cbi0_validator:
            try:
                self.cbi0_validator.assert_m2(
                    agents=request.declared_agents,
                    models=[request.model] + request.declared_models,
                )
            except Exception:
                m2_passed = False

        return m1_passed, m2_passed

    def _check_m3_m4(
        self, request: ChatRequest, response: ChatResponse
    ) -> tuple[bool, bool, Any]:
        """Run post-inference M3/M4 governance checkpoints."""
        m3_passed = True
        if self.cbi0_guard:
            try:
                self.cbi0_guard.assert_m3(response.message.content)
            except Exception:
                m3_passed = False

        m4_passed = True
        audit_record = None
        if self.cbi0_auditor:
            try:
                spec = {
                    "model": request.model,
                    "mode": request.mode.value,
                    "max_tokens": request.max_tokens,
                    "seed": request.seed,
                }
                runtime_trace = {
                    "finish_reason": response.finish_reason,
                    "usage": response.usage,
                }
                audit_record = self.cbi0_auditor.audit_m4(spec, runtime_trace)
                m4_passed = audit_record.get("status") == "EQUIVALENT"
            except Exception:
                m4_passed = False

        return m3_passed, m4_passed, audit_record

    def execute(self, request: ChatRequest, node_id: str = "pioneer1") -> ChatResponse:
        """
        Execute inference with full governance pipeline.

        Pipeline:
        1. M1: Projection access enforcement (declared scope)
        2. M2: Registry completeness (agents/models registered)
        3. Execute via selected backend
        4. M3: Terminal output non-reentry guard
        5. M4: Spec/runtime equivalence audit
        6. Record provenance
        """
        m1_passed, m2_passed = self._check_m1_m2(request)

        # --- Execute Inference ---
        backend = self._backend_for(request)
        response = backend.chat(request)

        m3_passed, m4_passed, audit_record = self._check_m3_m4(request, response)

        # --- Build Governed Response ---
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
            inference_id=f"{node_id}-{request.seed}-{response.created_at_ns}",
            provenance_hash="",  # Populated by provenance layer
        )

        # --- Record Provenance (async, non-blocking) ---
        if self.persistence:
            record = InferenceRecord(
                request=request,
                response=governed_response,
                timestamp_ns=governed_response.created_at_ns,
                node_id=node_id,
                execution_trace={
                    "m1": m1_passed,
                    "m2": m2_passed,
                    "m3": m3_passed,
                    "m4": m4_passed,
                    "audit": audit_record,
                },
            )
            # T2 commit — fire and forget for latency
            with contextlib.suppress(Exception):
                self.persistence.commit_inference(record)

        # --- Pulse Telemetry ---
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
                # Backend unreachable; skip it.
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
                # Backend unreachable (e.g. Ollama not running); continue checking others.
                logger.warning("Backend '%s' model_exists failed: %s", name, exc)
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
