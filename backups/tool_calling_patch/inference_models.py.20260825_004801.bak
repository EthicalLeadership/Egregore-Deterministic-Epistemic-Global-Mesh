"""Domain models for LLM inference — pure, no outer imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InferenceMode(StrEnum):
    DETERMINISTIC = "deterministic"  # Fixed seed, no randomness
    CREATIVE = "creative"  # Temperature allowed


@dataclass(frozen=True)
class ChatMessage:
    role: str  # system, user, assistant, tool
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    mode: InferenceMode = InferenceMode.DETERMINISTIC
    max_tokens: int = 2048
    seed: int = 42  # Ignored in CREATIVE mode
    stream: bool = False
    tools: list[dict[str, Any]] = field(default_factory=list)
    # CBI-0 scope declaration
    declared_agents: list[str] = field(default_factory=list)
    declared_models: list[str] = field(default_factory=list)
    # Optional GBNF grammar (string) for constrained sampling — critic use.
    grammar: str | None = None


@dataclass(frozen=True)
class ChatResponse:
    message: ChatMessage
    model: str
    created_at_ns: int
    usage: dict[str, int] = field(
        default_factory=dict
    )  # prompt_tokens, completion_tokens, total_tokens
    finish_reason: str = ""
    # CBI-0 audit trail
    m1_passed: bool = False
    m2_passed: bool = False
    m3_passed: bool = False
    m4_passed: bool = False
    # Provenance
    inference_id: str = ""
    provenance_hash: str = ""


@dataclass(frozen=True)
class InferenceRecord:
    """Canonical record for .zarc provenance."""

    request: ChatRequest
    response: ChatResponse
    timestamp_ns: int
    node_id: str = ""
    execution_trace: dict[str, Any] = field(default_factory=dict)
