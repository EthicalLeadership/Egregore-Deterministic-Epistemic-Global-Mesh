"""Chat-scoped inference orchestrator using Egregore's native model host."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from egregore.application.admission_controller import AdmissionDecision
from egregore.application.capacity_orchestrator import CapacityOrchestrator
from egregore.domain.units import DT, TU
from egregore.infrastructure.egregore_model_host import EgregoreModelHost
from egregore.infrastructure.gguf_catalog import GGUF_ROOT, GGUFCatalog
from egregore.interface.model_host_ports import InferenceRequest


@dataclass(frozen=True)
class ChatInferenceResult:
    ok: bool
    text: str
    model_id: str
    tokens_generated: int
    latency_ms: float
    dt_consumed: float
    placement_reason: str | None = None
    model_hash: str | None = None
    error: str | None = None


class ChatInferenceOrchestrator:
    """
    Thin wrapper that turns a chat prompt into an orchestrated inference request
    against Egregore's native GGUF model host.
    """

    def __init__(self, model_host: EgregoreModelHost | None = None) -> None:
        self._model_host = model_host or EgregoreModelHost()
        self._orchestrator = CapacityOrchestrator.build_default(
            total_dt=DT(10.0),
            total_tu=TU(100),
            epoch_duration_ms=1000,
        )
        self._orchestrator._model_host = self._model_host

    def is_available(self) -> bool:
        return self._model_host.is_available()

    def _model_size_bytes(self, model_id: str) -> int:
        catalog = GGUFCatalog()
        entry = catalog.get(model_id)
        if entry is not None:
            return entry.size_bytes
        # Fallback: read file size from disk.
        for tier in ["general", "expert", "specialized"]:
            candidate = GGUF_ROOT / tier / f"{model_id}.gguf"
            if candidate.exists():
                return candidate.stat().st_size
        return 1_000_000_000  # guess

    def ask(
        self,
        prompt: str,
        model_id: str | None = None,
        *,
        temperature: float = 0.0,
        top_p: float = 0.95,
        seed: int = 42,
        max_tokens: int = 256,
    ) -> ChatInferenceResult:
        if not self.is_available():
            return ChatInferenceResult(
                ok=False,
                text="",
                model_id=model_id or "unknown",
                tokens_generated=0,
                latency_ms=0.0,
                dt_consumed=0.0,
                error="Egregore model host is unavailable. Register a GGUF model first.",
            )

        resolved_model_id = (
            model_id
            or self._model_host._default_model_id
            or self._model_host.list_models()[0]
        )
        request = InferenceRequest(
            model_id=resolved_model_id,
            input_data=prompt.encode("utf-8"),
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            backend="egregore",
            priority=100,
        )

        try:
            model_size = self._model_size_bytes(resolved_model_id)
            decision, placement, work_unit_id = self._orchestrator.schedule_inference(
                request, model_size_bytes=model_size
            )
            if decision != AdmissionDecision.ADMITTED:
                return ChatInferenceResult(
                    ok=False,
                    text="",
                    model_id=resolved_model_id,
                    tokens_generated=0,
                    latency_ms=0.0,
                    dt_consumed=0.0,
                    placement_reason=placement.reason,
                    error=f"Inference rejected by orchestrator: {decision.name}",
                )

            result = self._model_host.generate(request, placement=placement)
            text = result.output_data.decode("utf-8", errors="replace")
            return ChatInferenceResult(
                ok=True,
                text=text,
                model_id=result.model_id,
                tokens_generated=result.tokens_generated,
                latency_ms=result.latency_ms,
                dt_consumed=result.dt_consumed.value,
                placement_reason=placement.reason,
            )
        except Exception as exc:
            return ChatInferenceResult(
                ok=False,
                text="",
                model_id=resolved_model_id,
                tokens_generated=0,
                latency_ms=0.0,
                dt_consumed=0.0,
                error=str(exc) or f"{type(exc).__name__}",
            )

    def list_models(self) -> list[str]:
        return self._model_host.list_models()

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> Iterator[str]:
        """Stream chat-formatted inference deltas through the capacity orchestrator."""
        if not self.is_available():
            yield "Egregore model host is unavailable. Register a GGUF model first."
            return

        resolved_model_id = (
            model_id
            or self._model_host._default_model_id
            or self._model_host.list_models()[0]
        )
        request = InferenceRequest(
            model_id=resolved_model_id,
            input_data=b"",
            max_tokens=max_tokens,
            temperature=temperature,
            backend="egregore",
            priority=100,
        )

        try:
            model_size = self._model_size_bytes(resolved_model_id)
            decision, placement, _work_unit_id = self._orchestrator.schedule_inference(
                request, model_size_bytes=model_size
            )
            if decision != AdmissionDecision.ADMITTED:
                yield f"Inference rejected by orchestrator: {decision.name}"
                return
            yield from self._model_host.stream_chat(
                request, placement=placement, messages=messages
            )
        except Exception as exc:
            yield f"Streaming inference failed: {exc}"

    def chat(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
    ) -> ChatInferenceResult:
        """Run chat-formatted inference using the model's built-in chat template."""
        if not self.is_available():
            return ChatInferenceResult(
                ok=False,
                text="",
                model_id=model_id or "unknown",
                tokens_generated=0,
                latency_ms=0.0,
                dt_consumed=0.0,
                error="Egregore model host is unavailable. Register a GGUF model first.",
            )

        resolved_model_id = (
            model_id
            or self._model_host._default_model_id
            or self._model_host.list_models()[0]
        )
        request = InferenceRequest(
            model_id=resolved_model_id,
            input_data=b"",
            max_tokens=2048,
            temperature=0.7,
            backend="egregore",
            priority=100,
        )

        try:
            model_size = self._model_size_bytes(resolved_model_id)
            decision, placement, work_unit_id = self._orchestrator.schedule_inference(
                request, model_size_bytes=model_size
            )
            if decision != AdmissionDecision.ADMITTED:
                return ChatInferenceResult(
                    ok=False,
                    text="",
                    model_id=resolved_model_id,
                    tokens_generated=0,
                    latency_ms=0.0,
                    dt_consumed=0.0,
                    placement_reason=placement.reason,
                    error=f"Inference rejected by orchestrator: {decision.name}",
                )

            result = self._model_host.chat(
                request, placement=placement, messages=messages
            )
            text = result.output_data.decode("utf-8", errors="replace")
            return ChatInferenceResult(
                ok=True,
                text=text,
                model_id=result.model_id,
                tokens_generated=result.tokens_generated,
                latency_ms=result.latency_ms,
                dt_consumed=result.dt_consumed.value,
                placement_reason=placement.reason,
            )
        except Exception as exc:
            return ChatInferenceResult(
                ok=False,
                text="",
                model_id=resolved_model_id,
                tokens_generated=0,
                latency_ms=0.0,
                dt_consumed=0.0,
                error=str(exc) or f"{type(exc).__name__}",
            )
