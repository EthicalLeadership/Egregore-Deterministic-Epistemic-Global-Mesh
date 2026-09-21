"""Tests for chat_interpreter WebSocket command handling."""

from __future__ import annotations

from typing import Any

from egregore.application.chat_interpreter import (
    ChatContext,
    _append_history_turn,
    _cmd_ask,
    _load_history,
)
from egregore.domain.inference_models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
)


class FakeInferenceService:
    """Minimal fake for the chat interpreter."""

    def __init__(self) -> None:
        self.clients: dict[str, Any] = {"fake": self}
        self.last_request: ChatRequest | None = None

    def execute(self, request: ChatRequest, node_id: str = "test") -> ChatResponse:
        self.last_request = request
        return ChatResponse(
            message=ChatMessage(role="assistant", content="reply"),
            model=request.model,
            created_at_ns=1,
            usage={},
            finish_reason="stop",
            inference_id="test-1",
        )

    def health(self) -> dict[str, Any]:
        return {"backends": {"fake": {"reachable": True}}}

    def model_exists(self, name: str) -> bool:
        return name == "fake-model"


def test_history_accumulates_across_asks() -> None:
    svc = FakeInferenceService()
    context = ChatContext(
        session_id="s1",
        user_id="u1",
        role="operator",
        env={"inference_service": svc, "CHAT_MODEL": "fake-model"},
    )

    r1 = _cmd_ask(["hello"], context)
    assert r1["ok"] is True
    assert len(_load_history(context)) == 2  # user + assistant

    r2 = _cmd_ask(["again"], context)
    assert r2["ok"] is True
    history = _load_history(context)
    assert len(history) == 4
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "hello"
    assert history[-1]["role"] == "assistant"

    # The second request should include prior turns.
    assert svc.last_request is not None
    roles = [m.role for m in svc.last_request.messages]
    assert roles.count("user") >= 2
    assert roles.count("assistant") >= 1


def test_history_respects_cap() -> None:
    context = ChatContext(session_id="s2", user_id="u1", role="operator", env={})
    for i in range(50):
        _append_history_turn(context, "user", f"msg-{i}")
        _append_history_turn(context, "assistant", f"reply-{i}")
    history = _load_history(context)
    assert len(history) <= 40
