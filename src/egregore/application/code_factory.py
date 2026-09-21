"""CodeFactoryService — Claude-powered code generation, review, and refactoring.

This service sits on top of the multi-backend InferenceService and defaults to
Anthropic Claude for code tasks. It provides opinionated prompts, deterministic
defaults, and structured output suitable for downstream tooling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from egregore.application.inference_service import InferenceService
from egregore.domain.inference_models import ChatMessage, ChatRequest, InferenceMode

DEFAULT_CODE_MODEL = "claude-3-5-sonnet-20241022"
DEFAULT_MAX_TOKENS = 4096


@dataclass(frozen=True)
class CodeTask:
    """Canonical input for a code-factory request."""

    task_type: str  # generate, review, refactor, explain, test
    prompt: str
    language: str = "python"
    context: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    model: str = DEFAULT_CODE_MODEL
    deterministic: bool = True


@dataclass(frozen=True)
class CodeArtifact:
    """Canonical output from a code-factory request."""

    task_type: str
    language: str
    model: str
    content: str
    usage: dict[str, int] = field(default_factory=dict)
    inference_id: str = ""
    governance: dict[str, bool] = field(default_factory=dict)


class CodeFactoryService:
    """Generate, review, and refactor code through the governed inference pipeline.

    The service routes all requests to the configured ``InferenceService``. When
    the requested model is a Claude identifier, the Anthropic backend is used;
    otherwise the request falls through to the default backend (native Coder).
    """

    def __init__(self, inference_service: InferenceService):
        self._inference = inference_service

    def _system_prompt(self, task: CodeTask) -> str:
        base = (
            f"You are an expert {task.language} software engineer. "
            "Produce concise, correct, production-ready code. "
            "Prefer clarity over cleverness. Include brief comments only where non-obvious."
        )
        if task.task_type == "review":
            base = (
                f"You are a senior {task.language} code reviewer. "
                "Identify bugs, security issues, performance concerns, and style problems. "
                "Be concise and actionable."
            )
        elif task.task_type == "refactor":
            base = (
                f"You are an expert {task.language} refactoring engineer. "
                "Improve the provided code without changing external behavior. "
                "Return the full refactored implementation."
            )
        elif task.task_type == "explain":
            base = (
                f"You are a technical educator explaining {task.language} code. "
                "Explain what the code does, step by step, at a moderate level of detail."
            )
        elif task.task_type == "test":
            base = (
                f"You are an expert {task.language} test engineer. "
                "Generate focused unit tests covering normal cases, edge cases, and error paths. "
                "Use mocks only when necessary."
            )

        if task.constraints:
            base += "\n\nConstraints:\n" + "\n".join(f"- {c}" for c in task.constraints)
        return base

    def _user_prompt(self, task: CodeTask) -> str:
        ctx = task.context
        parts: list[str] = [task.prompt]

        existing_code = ctx.get("existing_code")
        if existing_code:
            parts.append(f"\nExisting code:\n```{task.language}\n{existing_code}\n```")

        target_file = ctx.get("target_file")
        if target_file:
            parts.append(f"\nTarget file: {target_file}")

        return "\n".join(parts)

    def execute(self, task: CodeTask) -> CodeArtifact:
        """Execute a code task through the governed inference pipeline."""
        request = ChatRequest(
            model=task.model,
            messages=[
                ChatMessage(role="system", content=self._system_prompt(task)),
                ChatMessage(role="user", content=self._user_prompt(task)),
            ],
            mode=(
                InferenceMode.DETERMINISTIC
                if task.deterministic
                else InferenceMode.CREATIVE
            ),
            max_tokens=DEFAULT_MAX_TOKENS,
        )

        response = self._inference.execute(request)

        return CodeArtifact(
            task_type=task.task_type,
            language=task.language,
            model=response.model,
            content=response.message.content,
            usage=response.usage,
            inference_id=response.inference_id,
            governance={
                "m1_projection_access": response.m1_passed,
                "m2_registry_complete": response.m2_passed,
                "m3_non_reentry": response.m3_passed,
                "m4_spec_equivalence": response.m4_passed,
            },
        )

    def health(self) -> dict[str, Any]:
        """Return code-factory health, including backend reachability."""
        return {
            "default_code_model": DEFAULT_CODE_MODEL,
            "inference_health": self._inference.health(),
        }
