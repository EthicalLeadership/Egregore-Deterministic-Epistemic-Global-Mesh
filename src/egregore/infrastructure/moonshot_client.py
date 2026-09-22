"""Moonshot (Kimi) API client — concrete ILlmClient adapter (OpenAI-compatible endpoint)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any, cast

import requests

from egregore.domain.inference_models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    InferenceMode,
)

DEFAULT_MOONSHOT_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_MOONSHOT_MODEL = "kimi-k2.5"


def _moonshot_base_url() -> str:
    """Resolve Moonshot base URL from environment, with safe default."""
    return os.environ.get("KIMI_BASE_URL", DEFAULT_MOONSHOT_BASE_URL).rstrip("/")


def _moonshot_api_key() -> str:
    """Resolve Moonshot API key from environment."""
    return os.environ.get("KIMI_API_KEY", "")


class MoonshotClient:
    """HTTP client for the Moonshot (Kimi) API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
    ):
        self.api_key = api_key if api_key is not None else _moonshot_api_key()
        self.base_url = (base_url or _moonshot_base_url()).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        })

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("KIMI_API_KEY is not configured")
        url = f"{self.base_url}/chat/completions"
        r = self.session.post(url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def _chat_to_moonshot(self, request: ChatRequest) -> dict[str, Any]:
        """Convert Egregore ChatRequest to Moonshot / OpenAI chat-completions format."""
        messages = [
            {"role": msg.role, "content": msg.content} for msg in request.messages
        ]

        payload: dict[str, Any] = {
            "model": request.model or DEFAULT_MOONSHOT_MODEL,
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
        """Execute chat completion via Moonshot API."""
        payload = self._chat_to_moonshot(request)
        data = self._request(payload)

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        model = data.get("model", request.model or DEFAULT_MOONSHOT_MODEL)

        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        return ChatResponse(
            message=ChatMessage(role="assistant", content=content),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def stream_chat(self, request: ChatRequest) -> Iterator[ChatResponse]:
        """Stream chat completion via Moonshot API."""
        payload = self._chat_to_moonshot(request)
        payload["stream"] = True

        if not self.api_key:
            raise RuntimeError("KIMI_API_KEY is not configured")

        url = f"{self.base_url}/chat/completions"
        with self.session.post(
            url, json=payload, stream=True, timeout=self.timeout
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                token = line[6:]  # strip "data: "
                if token == "[DONE]":
                    break

                try:
                    chunk = cast(dict[str, Any], json.loads(token))
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield ChatResponse(
                            message=ChatMessage(
                                role="assistant",
                                content=content,
                            ),
                            model=chunk.get("model", DEFAULT_MOONSHOT_MODEL),
                            input_tokens=0,
                            output_tokens=0,
                        )
                except Exception:
                    continue

    def list_models(self) -> list[str]:
        """List available models from Moonshot (statically returned)."""
        return [
            "kimi-k2.5",
            "kimi-k2",
            "kimi-k2-base",
        ]

    def model_exists(self, model: str) -> bool:
        """Check if a model is available on Moonshot."""
        return any(
            model.lower().startswith(m.lower()) for m in self.list_models()
        )

    def health(self) -> bool:
        """Check API connectivity and key validity."""
        if not self.api_key:
            return False
        try:
            # Simple health check: list models via a minimal request
            payload = {
                "model": DEFAULT_MOONSHOT_MODEL,
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 1,
            }
            url = f"{self.base_url}/chat/completions"
            r = self.session.post(url, json=payload, timeout=10.0)
            r.raise_for_status()
            return True
        except Exception:
            return False
