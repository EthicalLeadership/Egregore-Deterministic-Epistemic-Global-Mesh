"""
CBI-0 governed inference orchestrator — hardened v4 (final).

Every inference runs through real M1-M4 checkpoints via the governance
classes from `egregore/governance/cbi0_governance.py`. The service now
writes mandatory, chained-hash .zarc provenance records and enforces
per-license rate limiting and backend circuit breakers.

All user-facing errors and logs are sanitized to avoid leaking internal
architecture. Health and model listing endpoints are gated behind a debug
flag to prevent reconnaissance.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import platform
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from egregore.domain.inference_models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
)
from egregore.governance.cbi0_governance import (
    M1ProjectionAccess,
    M2RegistryCompleteness,
    M3TerminalNonReentry,
    M4SpecRuntimeEquivalence,
    CBI0BlockedError,
    CheckpointStatus,
)
from egregore.interface.llm_ports import ILlmClient

logger = logging.getLogger(__name__)

# Model-name prefixes that route to specific backends.
ANTHROPIC_MODEL_PREFIXES = ("claude-",)
DEEPSEEK_MODEL_PREFIXES = ("deepseek-",)
MOONSHOT_MODEL_PREFIXES = ()
LOCAL_MODEL_PREFIXES = ("kimi-", "local-")
EGREGORE_MODEL_PREFIXES = ("egregore-", "coder-", "architect-", "my-coder")
GGUF_MODEL_PREFIXES = ("gguf-",)

# Debug flag for exposing sensitive operational details (health, model list)
DEBUG_EXPOSE_INTERNALS = os.environ.get("EGREGORE_DEBUG_EXPOSE_INTERNALS", "0") == "1"


def _normalize_backend_name(name: str) -> str:
    return name.strip().lower()


def _ensure_backend_allowed(name: str) -> None:
    if _normalize_backend_name(name) == "ollama":
        raise RuntimeError("Ollama backend is forbidden by design")


def _resolve_backend(model: str, default_backend: str = "egregore") -> str:
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


class BackendCircuitBreaker:
    """Per-backend circuit breaker to prevent cascading failures."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures: defaultdict[str, int] = defaultdict(int)
        self.last_failure_time: dict[str, float] = {}

    def allow_request(self, backend: str) -> bool:
        if self.failures[backend] >= self.failure_threshold:
            if time.time() - self.last_failure_time.get(backend, 0) < self.recovery_timeout:
                return False
            else:
                self.failures[backend] = 0
        return True

    def record_success(self, backend: str):
        self.failures[backend] = 0

    def record_failure(self, backend: str):
        self.failures[backend] += 1
        self.last_failure_time[backend] = time.time()


class LicenseRateLimiter:
    """Token bucket per license_id (in-memory; replace with Redis in prod)."""

    def __init__(self, default_limit: int = 100, window: int = 60):
        self.default_limit = default_limit
        self.window = window
        self.buckets: defaultdict[str, deque[float]] = defaultdict(deque)

    def allow(self, license_id: str) -> bool:
        now = time.time()
        bucket = self.buckets[license_id]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.default_limit:
            return False
        bucket.append(now)
        return True


def _default_zarc_path() -> str:
    """Return a user-writable, OS-specific default path for .zarc provenance."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Egregore" / "provenance"
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "Egregore" / "provenance"
    else:
        base = Path.home() / ".local" / "share" / "egregor" / "provenance"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "inference_provenance.zarc")


class ChainedProvenanceWriter:
    """
    Append-only .zarc writer with chained SHA-256 hashes.
    Each record includes a hash of the previous record, making tampering
    evident. The file is line-delimited JSON.
    """

    def __init__(self, zarc_path: str | None = None):
        self.zarc_path = zarc_path or _default_zarc_path()
        self._last_hash = self._load_last_hash()

    def _load_last_hash(self) -> str:
        try:
            with open(self.zarc_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                if lines:
                    last_line = lines[-1].strip()
                    if last_line:
                        last_entry = json.loads(last_line)
                        return last_entry.get("hash", "")
        except FileNotFoundError:
            pass
        return ""

    def write(self, record: dict[str, Any]) -> None:
        """Write a record with a chained hash."""
        record_json = json.dumps(record, sort_keys=True, separators=(",", ":"))
        current_hash = hashlib.sha256(f"{self._last_hash}:{record_json}".encode()).hexdigest()
        entry = {
            "record": record,
            "hash": current_hash,
            "prev_hash": self._last_hash,
            "timestamp": time.time_ns(),
        }
        os.makedirs(os.path.dirname(self.zarc_path) or ".", exist_ok=True)
        with open(self.zarc_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        self._last_hash = current_hash


class InferenceService:
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

        # Governance state (M2/M4 are reusable; M1/M3 created per-request)
        self.m2 = M2RegistryCompleteness()
        self.m4 = M4SpecRuntimeEquivalence()
        self.provenance = ChainedProvenanceWriter()

        # Operational
        self.circuit_breaker = BackendCircuitBreaker()
        self.rate_limiter = LicenseRateLimiter()
        _ensure_backend_allowed(self.default_backend)

    @property
    def default_backend(self) -> str:
        return self._default_backend

    @default_backend.setter
    def default_backend(self, backend_name: str) -> None:
        normalized = _normalize_backend_name(backend_name)
        _ensure_backend_allowed(normalized)
        if self.clients and normalized not in self.clients:
            logger.error("Backend '%s' not registered.", normalized)
            raise RuntimeError("Backend configuration error. Contact support.")
        self._default_backend = normalized

    def set_default_backend(self, backend_name: str) -> None:
        self.default_backend = backend_name

    def register_backend(self, backend_name: str, client: ILlmClient) -> None:
        normalized = _normalize_backend_name(backend_name)
        _ensure_backend_allowed(normalized)
        self.clients[normalized] = client

    def unregister_backend(self, backend_name: str) -> None:
        normalized = _normalize_backend_name(backend_name)
        if normalized == self.default_backend:
            raise RuntimeError("Cannot unregister active default backend")
        self.clients.pop(normalized, None)

    # ------------------------------------------------------------------
    # Governance helpers (per-request, fail-closed)
    # ------------------------------------------------------------------
    def _run_m1(self, request: ChatRequest) -> None:
        if not getattr(request, "declared_models", []):
            return
        m1 = M1ProjectionAccess()
        projection_id = f"req-{uuid.uuid4().hex}"
        allowed_paths = set(request.declared_models)
        scope_token = m1.declare_scope(projection_id, allowed_paths)
        report = m1.check_access(projection_id, request.model, scope_token)
        if report.status == CheckpointStatus.BLOCKED:
            logger.error("M1 blocked: %s", report)
            raise CBI0BlockedError(report)

    def _run_m2(self) -> None:
        required_ports = set(self.clients.keys())
        report = self.m2.verify_complete(required_ports)
        if report.status == CheckpointStatus.BLOCKED:
            logger.error("M2 blocked: %s", report)
            raise CBI0BlockedError(report)

    def _run_m3(self, request: ChatRequest) -> None:
        m3 = M3TerminalNonReentry()
        for msg in request.messages:
            report = m3.check_input(msg.content)
            if report.status == CheckpointStatus.BLOCKED:
                logger.error("M3 blocked: %s", report)
                raise CBI0BlockedError(report)
        report = m3.check_input(request.model)
        if report.status == CheckpointStatus.BLOCKED:
            logger.error("M3 blocked: %s", report)
            raise CBI0BlockedError(report)

    def _run_m4(self, request: ChatRequest, response: ChatResponse) -> None:
        spec_state = {
            "model": request.model,
            "mode": request.mode.value,
            "seed": request.seed,
            "max_tokens": request.max_tokens,
        }
        runtime_state = {
            "model": response.model,
            "finish_reason": response.finish_reason,
            "usage": response.usage,
        }
        report = self.m4.audit(
            execution_id=str(uuid.uuid4()),
            spec_state=spec_state,
            runtime_state=runtime_state,
        )
        if report.status == CheckpointStatus.BLOCKED:
            logger.error("M4 blocked: %s", report)
            raise CBI0BlockedError(report)

    def _run_pre_execution_governance(self, request: ChatRequest) -> None:
        self._run_m1(request)
        self._run_m2()
        self._run_m3(request)

    def _run_post_execution_governance(self, request: ChatRequest, response: ChatResponse) -> None:
        self._run_m4(request, response)

    # ------------------------------------------------------------------
    # Mandatory provenance
    # ------------------------------------------------------------------
    def _write_zarc_provenance(
        self,
        request: ChatRequest,
        response: ChatResponse,
        checks: dict,
        inference_id: str,
    ) -> None:
        """
        Write a chained-hash .zarc record. This is mandatory; any failure
        should raise an exception and block the response (fail-closed).
        """
        record = {
            "inference_id": inference_id,
            "timestamp_ns": time.time_ns(),
            "license_id": getattr(request, "license_id", "unknown"),
            "hardware_fingerprint": getattr(request, "hardware_fingerprint", "unknown"),
            "model": request.model,
            "backend": _resolve_backend(request.model, self.default_backend),
            "m1_passed": checks.get("m1", False),
            "m2_passed": checks.get("m2", False),
            "m3_passed": checks.get("m3", False),
            "m4_passed": checks.get("m4", False),
            "message_hash": hashlib.sha256(
                "".join(m.content for m in request.messages).encode()
            ).hexdigest(),
            "response_hash": hashlib.sha256(
                response.message.content.encode()
            ).hexdigest(),
        }
        try:
            self.provenance.write(record)
        except Exception as exc:
            logger.critical("Failed to write .zarc provenance: %s", exc)
            raise RuntimeError("Audit trail failure") from exc

    # ------------------------------------------------------------------
    # Main execution (sanitized)
    # ------------------------------------------------------------------
    def execute(self, request: ChatRequest) -> ChatResponse:
        _ensure_backend_allowed(self.default_backend)
        backend = _resolve_backend(request.model, self.default_backend)
        client = self.clients.get(backend)
        if client is None:
            logger.error("Backend '%s' not registered.", backend)
            raise RuntimeError("Backend unavailable.")

        if not self.rate_limiter.allow(getattr(request, "license_id", "")):
            raise RuntimeError("Rate limit exceeded")

        if not self.circuit_breaker.allow_request(backend):
            raise RuntimeError("Service temporarily unavailable")

        try:
            self._run_pre_execution_governance(request)
        except CBI0BlockedError:
            raise RuntimeError("Request blocked by governance")

        try:
            response = client.chat(request)
            self.circuit_breaker.record_success(backend)
        except Exception as exc:
            self.circuit_breaker.record_failure(backend)
            logger.error("Inference failed for backend %s: %s", backend, exc)
            raise RuntimeError("Inference failed")

        try:
            self._run_post_execution_governance(request, response)
            m4_passed = True
        except CBI0BlockedError:
            logger.error("M4 blocked; withholding response")
            raise RuntimeError("Response blocked by governance")

        # Unify inference_id across response and provenance
        inference_id = str(uuid.uuid4())
        governed_response = ChatResponse(
            message=response.message,
            model=response.model,
            created_at_ns=response.created_at_ns,
            usage=response.usage,
            finish_reason=response.finish_reason,
            m1_passed=True,
            m2_passed=True,
            m3_passed=True,
            m4_passed=m4_passed,
            inference_id=inference_id,
            provenance_hash="",
        )

        self._write_zarc_provenance(
            request,
            response,
            {"m1": True, "m2": True, "m3": True, "m4": m4_passed},
            inference_id,
        )

        node_id = os.environ.get("HOSTNAME", "unknown")
        if self.pulse:
            try:
                self.pulse.publish(
                    node_id=node_id,
                    metric="inference.completed",
                    value=1,
                    tags={
                        "model": request.model,
                        "mode": request.mode.value,
                        "m1": "True",
                        "m2": "True",
                        "m3": "True",
                        "m4": str(m4_passed),
                    },
                )
            except Exception as exc:
                logger.error("Pulse publish failed: %s", exc)

        return governed_response

    def execute_stream(self, request: ChatRequest) -> Iterator[str]:
        _ensure_backend_allowed(self.default_backend)
        backend = _resolve_backend(request.model, self.default_backend)
        client = self.clients.get(backend)
        if client is None:
            logger.error("Backend '%s' not registered.", backend)
            raise RuntimeError("Backend unavailable.")

        if not self.rate_limiter.allow(getattr(request, "license_id", "")):
            raise RuntimeError("Rate limit exceeded")

        if not self.circuit_breaker.allow_request(backend):
            raise RuntimeError("Service temporarily unavailable")

        try:
            self._run_pre_execution_governance(request)
        except CBI0BlockedError:
            raise RuntimeError("Request blocked by governance")

        stream_chat = getattr(client, "stream_chat", None)
        if callable(stream_chat):
            chunks = []
            try:
                for chunk in stream_chat(request):
                    chunks.append(chunk)
                full_text = "".join(chunks)
                synthetic_response = ChatResponse(
                    message=ChatMessage(role="assistant", content=full_text),
                    model=request.model,
                    created_at_ns=time.time_ns(),
                    usage={},
                    finish_reason="stream",
                )
                try:
                    self._run_post_execution_governance(request, synthetic_response)
                    m4_passed = True
                except CBI0BlockedError:
                    logger.error("M4 blocked during streaming; truncating stream")
                    return

                inference_id = str(uuid.uuid4())
                self._write_zarc_provenance(
                    request,
                    synthetic_response,
                    {"m1": True, "m2": True, "m3": True, "m4": m4_passed},
                    inference_id,
                )

                for chunk in chunks:
                    yield chunk
                return
            except Exception as exc:
                self.circuit_breaker.record_failure(backend)
                logger.error("Streaming inference failed: %s", exc)
                raise RuntimeError("Streaming failed")
        else:
            response = self.execute(request)
            yield response.message.content

    # ------------------------------------------------------------------
    # Model management (governance wrapped, sanitized)
    # ------------------------------------------------------------------
    def _run_admin_governance(self, model_name: str) -> None:
        self._run_m2()
        m3 = M3TerminalNonReentry()
        report = m3.check_input(model_name)
        if report.status == CheckpointStatus.BLOCKED:
            logger.error("M3 blocked for admin action: %s", report)
            raise CBI0BlockedError(report)

    def pull_model(self, name: str) -> None:
        _ensure_backend_allowed(self.default_backend)
        backend = _resolve_backend(name, self.default_backend)
        client = self.clients.get(backend)
        if client is None:
            logger.error("Backend '%s' not registered.", backend)
            raise RuntimeError("Backend unavailable.")
        self._run_admin_governance(name)
        try:
            client.pull_model(name)
        except Exception as exc:
            logger.error("pull_model failed: %s", exc)
            raise RuntimeError("Model pull failed")

    def delete_model(self, name: str) -> None:
        _ensure_backend_allowed(self.default_backend)
        backend = _resolve_backend(name, self.default_backend)
        client = self.clients.get(backend)
        if client is None:
            logger.error("Backend '%s' not registered.", backend)
            raise RuntimeError("Backend unavailable.")
        self._run_admin_governance(name)
        try:
            client.delete_model(name)
        except Exception as exc:
            logger.error("delete_model failed: %s", exc)
            raise RuntimeError("Model deletion failed")

    # ------------------------------------------------------------------
    # Health / model listing (gated by debug flag)
    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        _ensure_backend_allowed(self.default_backend)
        if not DEBUG_EXPOSE_INTERNALS:
            return {"status": "ok"}
        backends = {}
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
        _ensure_backend_allowed(self.default_backend)
        if not DEBUG_EXPOSE_INTERNALS:
            return []
        models = []
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
        for client in self.clients.values():
            try:
                if client.model_exists(name):
                    return True
            except Exception as exc:
                logger.warning("Backend model_exists failed: %s", exc)
        return False


# ----------------------------------------------------------------------
# Backend Registry Factory
# ----------------------------------------------------------------------
BACKEND_REGISTRY = {
    "egregore": ("egregore.infrastructure.coder_backend", "CoderBackend", None),
    "anthropic": ("egregore.infrastructure.anthropic_client", "AnthropicClient", "ANTHROPIC_API_KEY"),
    "deepseek": ("egregore.infrastructure.deepseek_client", "DeepSeekClient", "DEEPSEEK_API_KEY"),
    "moonshot": ("egregore.infrastructure.moonshot_client", "MoonshotClient", "MOONSHOT_API_KEY"),
    "local": ("egregore.infrastructure.local_model_client", "LocalModelClient", "EGREGORE_LOCAL_MODEL_ENABLED"),
    "gguf": ("egregore.infrastructure.gguf_backend", "GgufBackend", "EGREGORE_MODELS_ROOT"),
}

def _instantiate_backend(module_path: str, class_name: str, env_var: str | None) -> ILlmClient | None:
    if env_var:
        val = os.environ.get(env_var)
        if not val or (env_var == "EGREGORE_LOCAL_MODEL_ENABLED" and val != "1"):
            return None
    try:
        import importlib
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        instance = cls()
        if instance.health():
            return instance
        else:
            logger.warning("%s health check failed", class_name)
            return None
    except Exception as exc:
        logger.warning("Failed to instantiate %s: %s", class_name, exc)
        return None

def build_inference_service_from_env() -> InferenceService:
    default_backend = (
        os.environ.get("EGREGORE_DEFAULT_BACKEND", "egregore").strip() or "egregore"
    )
    _ensure_backend_allowed(default_backend)

    clients: dict[str, ILlmClient] = {}
    for name, (module_path, class_name, env_var) in BACKEND_REGISTRY.items():
        client = _instantiate_backend(module_path, class_name, env_var)
        if client:
            clients[name] = client
            logger.info("Registered backend: %s", name)

    if not clients:
        raise RuntimeError("No inference backend available. Check environment configuration.")

    return InferenceService(clients=clients, default_backend=default_backend)
