"""Egregore-native model host — loads GGUF models directly via llama-cpp-python."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from egregore.domain.units import DT, TU
from egregore.domain.work_unit import WorkUnitDemand
from egregore.infrastructure.gguf_catalog import GGUF_ROOT, GGUFCatalog
from egregore.infrastructure.local_llm_adapter import LocalLlmAdapter
from egregore.interface.model_host_ports import (
    InferenceRequest,
    InferenceResult,
)
from egregore.interface.placement_policy_ports import PlacementDecision


class EgregoreModelHost:
    """
    IModelHost implementation backed by the Egregore GGUF catalog and
    LocalLlmAdapter (llama-cpp-python).

    Models are loaded lazily and cached per model_id. The catalog decides
    which model file to use; this host executes generation and reports
    deterministic demand profiles to the orchestrator.
    """

    def __init__(
        self,
        catalog: GGUFCatalog | None = None,
        default_model_id: str | None = None,
        n_ctx: int = 8192,
    ) -> None:
        self._catalog = catalog or GGUFCatalog()
        self._default_model_id = default_model_id
        self._n_ctx = n_ctx
        self._adapters: dict[str, LocalLlmAdapter] = {}

    def _resolve_model_id(self, request_model_id: str | None) -> str:
        if request_model_id:
            return request_model_id
        if self._default_model_id:
            return self._default_model_id
        # Fall back to the first verified model in the catalog.
        available = [
            m
            for m, status in self._catalog.verify_all().items()
            if status == "VERIFIED"
        ]
        if not available:
            raise RuntimeError("No verified models available in Egregore catalog")
        return available[0]

    def _get_adapter(
        self, model_id: str, placement: PlacementDecision | None = None
    ) -> LocalLlmAdapter:
        cache_key = f"{model_id}:{placement.n_gpu_layers if placement else 'default'}:{placement.n_threads if placement else 'default'}"
        if cache_key not in self._adapters:
            entry = self._catalog.get(model_id)
            if entry is None:
                raise RuntimeError(f"Model '{model_id}' not found in Egregore catalog")
            model_path = Path(GGUF_ROOT) / entry.tier / entry.filename
            if not model_path.exists():
                raise RuntimeError(f"Model file not found: {model_path}")
            kwargs: dict[str, Any] = {
                "model_path": str(model_path),
                "n_ctx": self._n_ctx,
            }
            if placement is not None:
                kwargs["n_gpu_layers"] = placement.n_gpu_layers
                kwargs["n_threads"] = placement.n_threads
            self._adapters[cache_key] = LocalLlmAdapter(**kwargs)
        return self._adapters[cache_key]

    def is_available(self) -> bool:
        try:
            return any(
                status == "VERIFIED" for status in self._catalog.verify_all().values()
            )
        except Exception:
            return False

    def list_models(self) -> list[str]:
        return list(self._catalog._entries.keys())

    def get_demand_profile(self, request: InferenceRequest) -> WorkUnitDemand:
        """Return deterministic demand based on model size and token budget."""
        # Small models: low cost. Larger context/max_tokens: slightly higher TU.
        model_id = self._resolve_model_id(request.model_id)
        entry = self._catalog.get(model_id)
        # Base DT/TU cost; tuned so a default budget admits several inferences.
        size_gb = entry.size_bytes / (1024**3) if entry else 1.0
        dt_cost = max(0.1, min(2.0, size_gb * 0.3))
        tu_cost = max(1, request.max_tokens // 256)
        return WorkUnitDemand(
            dt=DT(dt_cost),
            tu=TU(tu_cost),
            priority=request.priority,
            max_wait_ms=30_000,
        )

    def generate(
        self,
        request: InferenceRequest,
        placement: PlacementDecision | None = None,
    ) -> InferenceResult:
        model_id = self._resolve_model_id(request.model_id)
        adapter = self._get_adapter(model_id, placement=placement)
        prompt = request.input_data.decode("utf-8")
        start = time.perf_counter()
        result = adapter.generate(
            prompt=prompt,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
            seed=request.seed,
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        output_data = result["text"].encode("utf-8")
        tokens_generated = len(output_data.split())  # best-effort token estimate
        demand = self.get_demand_profile(request)
        return InferenceResult(
            request_id=str(uuid.uuid4()),
            output_data=output_data,
            tokens_generated=tokens_generated,
            dt_consumed=demand.dt,
            latency_ms=latency_ms,
            model_id=model_id,
        )

    def stream_chat(
        self,
        request: InferenceRequest,
        placement: PlacementDecision | None = None,
        messages: list[dict[str, str]] | None = None,
    ) -> Iterator[str]:
        """Stream chat-completion deltas for the requested model."""
        model_id = self._resolve_model_id(request.model_id)
        adapter = self._get_adapter(model_id, placement=placement)
        yield from adapter.stream_chat(
            messages=messages or [],
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )

    def chat(
        self,
        request: InferenceRequest,
        placement: PlacementDecision | None = None,
        messages: list[dict[str, str]] | None = None,
    ) -> InferenceResult:
        """Chat-completion path using the model's built-in chat template."""
        model_id = self._resolve_model_id(request.model_id)
        adapter = self._get_adapter(model_id, placement=placement)
        start = time.perf_counter()
        result = adapter.chat(
            messages=messages or [],
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        output_data = result["text"].encode("utf-8")
        tokens_generated = len(output_data.split())  # best-effort token estimate
        demand = self.get_demand_profile(request)
        return InferenceResult(
            request_id=str(uuid.uuid4()),
            output_data=output_data,
            tokens_generated=tokens_generated,
            dt_consumed=demand.dt,
            latency_ms=latency_ms,
            model_id=model_id,
        )
