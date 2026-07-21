"""DeepSeek API client — concrete ILlmClient adapter (OpenAI-compatible endpoint)."""

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

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"


def _deepseek_base_url() -> str:
    """Resolve DeepSeek base URL from environment, with safe default."""
    return os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL).rstrip("/")


def _deepseek_api_key() -> str:
    """Resolve DeepSeek API key from environment."""
    return os.environ.get("DEEPSEEK_API_KEY", "")


class DeepSeekClient:
    """HTTP client for the DeepSeek API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
    ):
        self.api_key = api_key if api_key is not None else _deepseek_api_key()
        self.base_url = (base_url or _deepseek_base_url()).rstrip("/")
        self.timeout = timeout
        self._client: httpx.Client | None = None

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            headers = {
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            }
            self._client = httpx.Client(
                base_url=self.base_url, headers=headers, timeout=self.timeout
            )
        return self._client

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        client = self._ensure_client()
        r = client.post("/v1/chat/completions", json=payload)
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def _chat_to_deepseek(self, request: ChatRequest) -> dict[str, Any]:
        """Convert Egregore ChatRequest to DeepSeek / OpenAI chat-completions format."""
        messages = [
            {"role": msg.role, "content": msg.content} for msg in request.messages
        ]

        payload: dict[str, Any] = {
            "model": request.model or DEFAULT_DEEPSEEK_MODEL,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }

        if request.mode == InferenceMode.DETERMINISTIC:
            payload["temperature"] = 0.0
            payload["top_p"] = 1.0
        else:
            payload["temperature"] = 0.7

        if request.stream:
            payload["stream"] = True

        if request.tools:
            payload["tools"] = request.tools

        return payload

    def chat(self, request: ChatRequest) -> ChatResponse:
        """Execute chat completion via DeepSeek API."""
        payload = self._chat_to_deepseek(request)
        data = self._request(payload)

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        if not isinstance(content, str):
            content = str(content)

        usage = data.get("usage", {})
        created_at_ns = int(time.time() * 1e9)

        return ChatResponse(
            message=ChatMessage(role="assistant", content=content),
            model=data.get("model", request.model),
            created_at_ns=created_at_ns,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            finish_reason=choice.get("finish_reason", "unknown"),
        )

    def stream_chat(self, request: ChatRequest) -> Iterator[str]:
        """Stream chat completion from DeepSeek (OpenAI-compatible) API.

        Yields raw text deltas. The caller is responsible for SSE formatting.
        """
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")

        payload = self._chat_to_deepseek(request)
        payload["stream"] = True
        client = self._ensure_client()

        with client.stream("POST", "/v1/chat/completions", json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line == "data: [DONE]":
                    return
                if not line or not line.startswith("data: "):
                    continue
                try:
                    event = canonical_loads(line.removeprefix("data: "))
                except json.JSONDecodeError:
                    continue
                delta = event.get("choices", [{}])[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield text

    def generate(self, prompt: str, model: str | None = None) -> str:
        """Single-turn text generation."""
        request = ChatRequest(
            model=model or DEFAULT_DEEPSEEK_MODEL,
            messages=[ChatMessage(role="user", content=prompt)],
            mode=InferenceMode.DETERMINISTIC,
            max_tokens=2048,
        )
        return self.chat(request).message.content

    def list_models(self) -> list[dict[str, Any]]:
        """DeepSeek does not expose a public list-models endpoint; return known models."""
        return [
            {"name": "deepseek-chat"},
            {"name": "deepseek-reasoner"},
        ]

    def list_model_names(self) -> list[str]:
        return [m["name"] for m in self.list_models()]

    def pull_model(self, name: str) -> None:
        """No-op for DeepSeek: models are hosted remotely."""
        return None

    def delete_model(self, name: str) -> None:
        """No-op for DeepSeek: models are hosted remotely."""
        return None

    def model_exists(self, name: str) -> bool:
        """Check against known DeepSeek model identifiers or any deepseek-* name."""
        if not name:
            return False
        lower = name.lower()
        return lower.startswith("deepseek-") or lower in (
            m.lower() for m in self.list_model_names()
        )

    def health(self) -> bool:
        """Report healthy when an API key is configured.

        DeepSeek does not expose a reliable unauthenticated health endpoint,
        so we treat a configured key as "reachable" and rely on chat calls to
        surface auth/network errors.
        """
        return bool(self.api_key)
