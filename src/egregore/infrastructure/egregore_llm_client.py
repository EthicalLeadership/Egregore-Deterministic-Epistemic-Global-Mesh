"""Egregore-native LLM client — ILlmClient adapter for the Egregore Model Service (EMS).

Talks directly to llama-server instances managed by EmsLifecycle, or falls back to
llama-cpp-python via LocalLlmAdapter for in-process inference.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any

import httpx

from egregore.domain.inference_models import ChatMessage, ChatRequest, ChatResponse
from egregore.interface.llm_ports import ILlmClient

DEFAULT_EMS_BASE_URL = os.environ.get(
    "EGREGORE_EMS_URL", "http://127.0.0.1:8001"
)
DEFAULT_API_KEY = os.environ.get("EGREGORE_EMS_API_KEY", "egregore-local")


class EgregoreLlmClient(ILlmClient):
    """ILlmClient backend for the Egregore Model Service.

    Communicates with llama-server instances via the EMS proxy or directly.
    Supports both chat completions and streaming.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url or DEFAULT_EMS_BASE_URL).rstrip("/")
        self.api_key = api_key or DEFAULT_API_KEY
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

    # ------------------------------------------------------------------
    # ILlmClient protocol
    # ------------------------------------------------------------------
    def chat(self, request: ChatRequest) -> ChatResponse:
        payload = self._request_payload(request)
        client = self._ensure_client()
        r = client.post("/v1/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        usage = data.get("usage", {})

        return ChatResponse(
            message=ChatMessage(role="assistant", content=content),
            model=data.get("model", request.model),
            created_at_ns=time.time_ns(),
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            finish_reason=choice.get("finish_reason", "stop"),
        )

    def stream_chat(self, request: ChatRequest) -> Iterator[str]:
        payload = self._request_payload(request)
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
                    event = json.loads(line.removeprefix("data: "))
                except json.JSONDecodeError:
                    continue
                delta = event.get("choices", [{}])[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield text

    def generate(self, prompt: str, model: str | None = None) -> str:
        request = ChatRequest(
            model=model or "default",
            messages=[ChatMessage(role="user", content=prompt)],
            mode="deterministic",
            max_tokens=2048,
        )
        return self.chat(request).message.content

    def list_models(self) -> list[dict[str, Any]]:
        try:
            client = self._ensure_client()
            r = client.get("/v1/models")
            r.raise_for_status()
            data = r.json()
            return data.get("data", [])
        except Exception:
            return []

    def health(self) -> bool:
        try:
            client = self._ensure_client()
            r = client.get("/health")
            return r.status_code == 200
        except Exception:
            return False

    def model_exists(self, name: str) -> bool:
        return any(m.get("id") == name for m in self.list_models())

    def pull_model(self, name: str) -> None:
        raise NotImplementedError(
            "EgregoreLlmClient does not support pulling. "
            "Register the model with: egregor model register <id> <path>"
        )

    def delete_model(self, name: str) -> None:
        raise NotImplementedError(
            "EgregoreLlmClient does not support deletion. "
            "Use the registry CLI to unregister."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _request_payload(self, request: ChatRequest) -> dict[str, Any]:
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if request.mode.value == "deterministic":
            payload["temperature"] = 0.0
            payload["top_p"] = 1.0
        else:
            payload["temperature"] = 0.7
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.tools:
            payload["tools"] = request.tools
        return payload
