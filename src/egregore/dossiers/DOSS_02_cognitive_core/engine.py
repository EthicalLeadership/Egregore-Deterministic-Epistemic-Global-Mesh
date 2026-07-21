"""DOSS-02: Cognitive Core Engine — Inference routing and orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class InferenceBackend(Protocol):
    def infer(self, prompt: str) -> str: ...
    def health(self) -> bool: ...


@dataclass
class ChatRequest:
    model: str
    messages: list[dict[str, str]]
    temperature: float = 0.7


@dataclass
class ChatResponse:
    content: str
    model: str
    tokens_used: int = 0


class CognitiveCoreEngine:
    """Central inference router for the Egregore cognitive layer."""

    def __init__(self, backends: dict[str, InferenceBackend]) -> None:
        self.backends = backends

    def _resolve_backend(self, model: str) -> InferenceBackend:
        family = model.split("-")[0].lower()
        mapping = {
            "claude": "anthropic",
            "deepseek": "deepseek",
            "qwen": "local",
            "llama": "local",
        }
        backend_name = mapping.get(family, "local")
        if backend_name not in self.backends:
            raise ValueError(f"No backend available for model: {model}")
        return self.backends[backend_name]

    def execute(self, request: ChatRequest) -> ChatResponse:
        backend = self._resolve_backend(request.model)
        prompt = request.messages[-1]["content"] if request.messages else ""
        result = backend.infer(prompt)
        # Estimate tokens: rough heuristic (4 chars ≈ 1 token)
        tokens_used = max(1, len(prompt) // 4 + len(result) // 4)
        return ChatResponse(
            content=result, model=request.model, tokens_used=tokens_used
        )

    def health(self) -> dict[str, bool]:
        return {name: backend.health() for name, backend in self.backends.items()}
