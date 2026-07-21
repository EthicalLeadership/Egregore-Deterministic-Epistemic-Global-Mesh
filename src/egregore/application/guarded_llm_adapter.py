"""GuardedLlmAdapter — ExecutionGuard wrapper around LocalLlmAdapter."""

from typing import Any

from egregore.application.execution_guard import ExecutionGuard
from egregore.domain.execution_context import ExecutionContext


class GuardedLlmAdapter:
    """Facade: every LLM inference call passes through ExecutionGuard."""

    def __init__(self, inner_adapter: Any, context: ExecutionContext):
        self._inner = inner_adapter
        self._context = context

    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> str:
        deterministic = temperature == 0.0
        ctx = ExecutionContext(
            tenant_id=self._context.tenant_id,
            user_id=self._context.user_id,
            role=self._context.role,
            session_id=self._context.session_id,
            trace_id=self._context.trace_id,
            subsystem="model",
            operation="llm_generate",
            metadata={
                "model": getattr(self._inner, "model_name", "unknown"),
                "max_tokens": max_tokens,
                "temperature": temperature,
                "deterministic": deterministic,
                "prompt_hash": hash(prompt) & 0xFFFFFFFF,
            },
        )
        return ExecutionGuard.execute(
            context=ctx,
            handler=self._inner.generate,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )

    def batch_generate(
        self,
        prompts: list[str],
        max_tokens: int = 512,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> list[str]:
        ctx = ExecutionContext(
            tenant_id=self._context.tenant_id,
            user_id=self._context.user_id,
            role=self._context.role,
            session_id=self._context.session_id,
            trace_id=self._context.trace_id,
            subsystem="model",
            operation="llm_batch_generate",
            metadata={"batch_size": len(prompts), "temperature": temperature},
        )
        return ExecutionGuard.execute(
            context=ctx,
            handler=self._inner.batch_generate,
            prompts=prompts,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )

    @property
    def model_name(self) -> str:
        return getattr(self._inner, "model_name", "unknown")
