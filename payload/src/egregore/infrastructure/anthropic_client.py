"""Anthropic Claude API client — concrete ILlmClient adapter."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any, cast

import httpx

from egregore.domain.inference_models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    InferenceMode,
)
from egregore.shared.canonical import canonical_loads

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-sonnet-20241022"


class AnthropicClient:
    """HTTP client for the Anthropic Messages API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url = (
            base_url or os.environ.get("ANTHROPIC_BASE_URL", DEFAULT_ANTHROPIC_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout
        self._client: httpx.Client | None = None

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            self._client = httpx.Client(
                base_url=self.base_url, headers=headers, timeout=self.timeout
            )
        return self._client

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        client = self._ensure_client()
        r = client.post("/v1/messages", json=payload)
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def _chat_to_anthropic(self, request: ChatRequest) -> dict[str, Any]:
        """Convert Egregore ChatRequest to Anthropic Messages format."""
        system_parts: list[str] = []
        messages: list[dict[str, str]] = []
        for msg in request.messages:
            if msg.role == "system":
                system_parts.append(msg.content)
            else:
                messages.append({"role": msg.role, "content": msg.content})

        payload: dict[str, Any] = {
            "model": request.model or DEFAULT_ANTHROPIC_MODEL,
            "max_tokens": request.max_tokens,
            "messages": messages,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)

        if request.mode == InferenceMode.DETERMINISTIC:
            payload["temperature"] = 0.0
            payload["top_p"] = 1.0
        else:
            payload["temperature"] = 0.7

        return payload

    def chat(self, request: ChatRequest) -> ChatResponse:
        """Execute chat completion via Anthropic Messages API."""
        payload = self._chat_to_anthropic(request)
        data = self._request(payload)

        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        usage = data.get("usage", {})
        created_at_ns = time.time_ns()

        return ChatResponse(
            message=ChatMessage(role="assistant", content=content),
            model=data.get("model", request.model),
            created_at_ns=created_at_ns,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0)
                + usage.get("output_tokens", 0),
            },
            finish_reason=data.get("stop_reason", "unknown"),
        )

    def stream_chat(self, request: ChatRequest) -> Iterator[str]:
        """Stream chat completion from Anthropic Messages API.

        Yields raw text deltas. The caller is responsible for SSE formatting.
        """
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")

        payload = self._chat_to_anthropic(request)
        payload["stream"] = True
        client = self._ensure_client()

        with client.stream("POST", "/v1/messages", json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    event = canonical_loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text":
                        yield delta.get("text", "")

    def generate(self, prompt: str, model: str | None = None) -> str:
        """Single-turn text generation."""
        request = ChatRequest(
            model=model or DEFAULT_ANTHROPIC_MODEL,
            messages=[ChatMessage(role="user", content=prompt)],
            mode=InferenceMode.DETERMINISTIC,
            max_tokens=2048,
        )
        return self.chat(request).message.content

    def list_models(self) -> list[dict[str, Any]]:
        """Anthropic does not expose a public list-models endpoint; return known models."""
        return [
            {"name": "claude-3-5-sonnet-20241022"},
            {"name": "claude-3-opus-20240229"},
            {"name": "claude-3-haiku-20240307"},
        ]

    def list_model_names(self) -> list[str]:
        return [m["name"] for m in self.list_models()]

    def pull_model(self, name: str) -> None:
        """No-op for Anthropic: models are hosted remotely."""
        return None

    def delete_model(self, name: str) -> None:
        """No-op for Anthropic: models are hosted remotely."""
        return None

    def model_exists(self, name: str) -> bool:
        """Check against known Claude model identifiers or any claude-* name."""
        if not name:
            return False
        lower = name.lower()
        return lower.startswith("claude-") or lower in (
            m.lower() for m in self.list_model_names()
        )

    def health(self) -> bool:
        """Report healthy when an API key is configured.

        Anthropic does not expose a reliable unauthenticated health endpoint,
        so we treat a configured key as "reachable" and rely on chat calls to
        surface auth/network errors.
        """
        return bool(self.api_key)
