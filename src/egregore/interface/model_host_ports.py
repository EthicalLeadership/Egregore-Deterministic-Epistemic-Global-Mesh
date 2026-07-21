"""
BLACKSTAR LAW: Model Host Ports
AI inference abstraction. Plane 2 only — read-only derivatives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from egregore.domain.units import DT
from egregore.domain.work_unit import WorkUnitDemand


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    model_id: str
    input_data: bytes
    max_tokens: int = 512
    temperature: float = 0.7
    top_p: float = 1.0
    seed: int = 42
    backend: str = "llama.cpp"
    priority: int = 100


@dataclass(frozen=True, slots=True)
class InferenceResult:
    request_id: str
    output_data: bytes
    tokens_generated: int
    dt_consumed: DT
    latency_ms: float
    model_id: str


@runtime_checkable
class IModelHost(Protocol):
    """Port for AI model inference backends."""

    def generate(self, request: InferenceRequest) -> InferenceResult: ...

    def get_demand_profile(self, request: InferenceRequest) -> WorkUnitDemand: ...

    def is_available(self) -> bool: ...

    def list_models(self) -> list[str]: ...


@runtime_checkable
class IModelRegistry(Protocol):
    """Port for model catalog management."""

    def register_model(self, model_id: str, path: str, backend: str) -> None: ...

    def get_model_path(self, model_id: str) -> str | None: ...

    def list_registered(self) -> list[str]: ...
