"""Tests for CodeFactoryService."""

from __future__ import annotations

from unittest.mock import MagicMock

from egregore.application.code_factory import (
    DEFAULT_CODE_MODEL,
    CodeFactoryService,
    CodeTask,
)
from egregore.application.inference_service import InferenceService
from egregore.domain.inference_models import ChatMessage, ChatResponse


def make_inference_service_stub(
    content: str, model: str = DEFAULT_CODE_MODEL
) -> InferenceService:
    stub_client = MagicMock()
    stub_client.chat.return_value = ChatResponse(
        message=ChatMessage(role="assistant", content=content),
        model=model,
        created_at_ns=1,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        finish_reason="stop",
    )
    stub_client.health.return_value = True
    stub_client.list_models.return_value = []
    return InferenceService({"anthropic": stub_client}, default_backend="anthropic")


def test_generate_task_routes_to_claude() -> None:
    inference = make_inference_service_stub("def hello():\n    pass")
    factory = CodeFactoryService(inference)

    artifact = factory.execute(
        CodeTask(task_type="generate", prompt="write a hello function")
    )

    assert artifact.task_type == "generate"
    assert artifact.language == "python"
    assert artifact.model == DEFAULT_CODE_MODEL
    assert "def hello" in artifact.content
    assert artifact.governance["m1_projection_access"] is True


def test_system_prompt_changes_by_task_type() -> None:
    inference = make_inference_service_stub("review result")
    factory = CodeFactoryService(inference)

    artifact = factory.execute(CodeTask(task_type="review", prompt="review this"))

    assert artifact.task_type == "review"
    # Verify the stub was called with a chat request containing the review system prompt.
    stub_client = inference.clients["anthropic"]
    request = stub_client.chat.call_args[0][0]
    assert request.messages[0].role == "system"
    assert "code reviewer" in request.messages[0].content


def test_context_existing_code_is_included() -> None:
    inference = make_inference_service_stub("refactored")
    factory = CodeFactoryService(inference)

    task = CodeTask(
        task_type="refactor",
        prompt="refactor",
        context={"existing_code": "def old(): return 1"},
    )
    factory.execute(task)

    stub_client = inference.clients["anthropic"]
    request = stub_client.chat.call_args[0][0]
    user_msg = request.messages[1].content
    assert "Existing code:" in user_msg
    assert "def old(): return 1" in user_msg


def test_constraints_are_appended_to_system_prompt() -> None:
    inference = make_inference_service_stub("code")
    factory = CodeFactoryService(inference)

    task = CodeTask(
        task_type="generate",
        prompt="write code",
        constraints=["no external dependencies", "type hints"],
    )
    factory.execute(task)

    stub_client = inference.clients["anthropic"]
    request = stub_client.chat.call_args[0][0]
    system = request.messages[0].content
    assert "Constraints:" in system
    assert "no external dependencies" in system
