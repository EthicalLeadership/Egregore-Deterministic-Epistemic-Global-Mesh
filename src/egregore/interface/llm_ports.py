from typing import Any, Protocol, runtime_checkable

from egregore.domain.inference_models import ChatRequest, ChatResponse


@runtime_checkable
class ILlmClient(Protocol):
    """Generic port for an LLM chat backend.

    Application and HTTP layers depend on this abstraction, NOT on concrete
    infrastructure implementations (Anthropic, local, DeepSeek, etc.).
    """

    def chat(self, request: ChatRequest) -> ChatResponse: ...

    def generate(self, prompt: str, model: str | None = None) -> str:
        """Single-turn text generation fallback."""
        ...

    def list_models(self) -> list[dict[str, Any]]:
        """Return metadata for models available through this backend."""
        ...

    def health(self) -> bool:
        """Return True when the backend is reachable and ready."""
        ...

    def pull_model(self, name: str) -> None:
        """Pull/adopt a model into this backend, if supported."""
        ...

    def delete_model(self, name: str) -> None:
        """Delete a model from this backend, if supported."""
        ...

    def model_exists(self, name: str) -> bool:
        """Check whether a model identifier is available through this backend."""
        ...
