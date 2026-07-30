"""EMS Proxy — Unified OpenAI-compatible inference endpoint.

Exposes a single :8001/v1/chat/completions that routes to the correct
in-process Egregore model backend based on the `model` field in the request.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from egregore.domain.inference_models import ChatMessage, ChatRequest, InferenceMode
from egregore.ems.lifecycle import EmsLifecycle
from egregore.ems.prompts import maybe_format_messages
from egregore.ems.registry import EmsRegistry, ModelStatus

DEFAULT_PROXY_PORT = int(os.environ.get("EGREGORE_EMS_PROXY_PORT", "8001"))
DEFAULT_PROXY_HOST = os.environ.get("EGREGORE_EMS_PROXY_HOST", "0.0.0.0")
DEFAULT_REQUEST_TIMEOUT = float(os.environ.get("EGREGORE_EMS_TIMEOUT", "120.0"))
DEFAULT_AUTO_START = os.environ.get("EGREGORE_EMS_AUTO_START", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


class EmsProxyError(RuntimeError):
    """Raised when the proxy cannot route or forward a request."""


class EmsProxy:
    """Reverse proxy for model-specific inference backends.

    Usage (standalone):
        proxy = EmsProxy(registry)
        uvicorn.run(proxy.app, host="0.0.0.0", port=8001)

    Usage (mounted in existing FastAPI app):
        from egregore.ems.proxy import build_proxy_router
        app.include_router(build_proxy_router(registry), prefix="/v1")

    When a ``native_backend`` is supplied, the proxy uses it directly for the
    active Coder model instead of loading a second copy via the lifecycle.
    """

    def __init__(
        self,
        registry: EmsRegistry,
        *,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        auto_start: bool = DEFAULT_AUTO_START,
        auto_start_timeout: float = 300.0,
        native_backend: Any | None = None,
    ) -> None:
        self.registry = registry
        self.timeout = timeout
        self.auto_start = auto_start
        self.auto_start_timeout = auto_start_timeout
        self._native_backend = native_backend
        self._lifecycle = EmsLifecycle(registry, health_timeout=max(300.0, auto_start_timeout))

    # ------------------------------------------------------------------
    # Routing / formatting logic
    # ------------------------------------------------------------------
    def _format_payload(
        self, model_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply model-specific chat formatting to the request payload."""
        rec = self.registry.get(model_id)
        chat_template = rec.chat_template if rec else ""
        messages = payload.get("messages", [])
        if isinstance(messages, list) and chat_template:
            payload = {**payload}
            payload["messages"] = maybe_format_messages(
                model_id, chat_template, messages
            )
        return payload

    def _resolve_backend(self, model_id: str) -> Any:
        """Return a healthy backend, or None if not loaded."""
        rec = self.registry.get(model_id)
        # Use the shared in-process Egregore backend if provided and the model
        # is registered as native.
        if (
            self._native_backend is not None
            and rec is not None
            and rec.backend_type == "native"
            and self._native_backend.health()
        ):
            return self._native_backend
        backend = self._lifecycle.get_backend(model_id)
        if backend is not None and backend.health():
            return backend
        return None

    def _list_available_models(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible /v1/models list."""
        models: list[dict[str, Any]] = []
        for rec in self.registry.list_models():
            models.append(
                {
                    "id": rec.model_id,
                    "object": "model",
                    "created": int(rec.created_at) if rec.created_at else 0,
                    "owned_by": rec.node,
                    "meta": {
                        "version": rec.version,
                        "tier": rec.tier,
                        "backend_type": rec.backend_type,
                        "parameters": rec.parameters,
                        "status": rec.status.value,
                    },
                }
            )
        return models

    @staticmethod
    def _build_chat_request(model_id: str, payload: dict[str, Any]) -> ChatRequest:
        """Convert an OpenAI-style payload into a ChatRequest."""
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="'messages' must be a list")

        chat_messages: list[ChatMessage] = []
        for m in messages:
            if isinstance(m, dict) and "role" in m and "content" in m:
                chat_messages.append(ChatMessage(role=m["role"], content=m["content"]))
            else:
                raise HTTPException(status_code=400, detail="Invalid message format")

        mode = InferenceMode.DETERMINISTIC
        if payload.get("temperature", 0.0) > 0:
            mode = InferenceMode.CREATIVE

        return ChatRequest(
            model=model_id,
            messages=chat_messages,
            mode=mode,
            max_tokens=payload.get("max_tokens", 2048),
            seed=payload.get("seed", 42),
            stream=payload.get("stream", False),
            tools=payload.get("tools", []),
        )

    @staticmethod
    def _build_openai_response(model_id: str, response: Any) -> dict[str, Any]:
        """Convert a ChatResponse into an OpenAI-compatible JSON dict."""
        usage = response.usage or {}
        return {
            "id": f"chatcmpl-{response.created_at_ns}",
            "object": "chat.completion",
            "created": int(response.created_at_ns / 1_000_000_000),
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": response.message.role,
                        "content": response.message.content,
                    },
                    "finish_reason": response.finish_reason or "stop",
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }

    # ------------------------------------------------------------------
    # HTTP handlers (FastAPI)
    # ------------------------------------------------------------------
    def _build_app(self) -> Any:
        """Lazily create FastAPI app."""
        app = FastAPI(title="Egregore Model Service Proxy")

        @app.get("/v1/models")
        async def list_models() -> JSONResponse:
            return JSONResponse({"object": "list", "data": self._list_available_models()})

        @app.post("/v1/chat/completions")
        async def chat_completions(request: Request) -> Any:
            body = await request.body()
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="Invalid JSON") from exc

            model_id = payload.get("model")
            if not model_id:
                raise HTTPException(status_code=400, detail="Missing 'model' field")

            # Resolve backend (auto-start if enabled)
            backend = self._resolve_backend(model_id)
            if backend is None and self.auto_start:
                started = await self._try_auto_start(model_id)
                if started:
                    backend = self._resolve_backend(model_id)

            if backend is None:
                rec = self.registry.get(model_id)
                if rec is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Model '{model_id}' is not registered.",
                    )
                raise HTTPException(
                    status_code=503,
                    detail=f"Model '{model_id}' is {rec.status.value}.",
                )

            # Apply model-specific chat formatting before inference
            payload = self._format_payload(model_id, payload)

            chat_request = self._build_chat_request(model_id, payload)
            try:
                response = backend.chat(chat_request)
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Inference error: {exc}") from exc

            return JSONResponse(self._build_openai_response(model_id, response))

        @app.get("/health")
        async def health() -> JSONResponse:
            return JSONResponse(self.registry.health())

        @app.get("/v1/ems/status")
        async def ems_status() -> JSONResponse:
            return JSONResponse(
                {
                    "models": [r.to_dict() for r in self.registry.list_models()],
                    "proxy": {
                        "auto_start": self.auto_start,
                        "timeout": self.timeout,
                    },
                }
            )

        return app

    async def _try_auto_start(self, model_id: str) -> bool:
        """Trigger lifecycle start and wait for the model to become RUNNING."""
        try:
            self._lifecycle.start(model_id)
        except Exception:
            return False

        deadline = time.time() + self.auto_start_timeout
        while time.time() < deadline:
            rec = self.registry.get(model_id)
            if rec and rec.status == ModelStatus.RUNNING:
                return True
            await self._async_sleep(0.5)
        return False

    @staticmethod
    async def _async_sleep(seconds: float) -> None:
        import asyncio

        await asyncio.sleep(seconds)

    @property
    def app(self) -> Any:
        return self._build_app()


def build_proxy_from_env(registry: EmsRegistry | None = None) -> EmsProxy:
    """Factory: create proxy from environment."""
    if registry is None:
        registry = build_registry_from_env()
    return EmsProxy(
        registry=registry,
        timeout=DEFAULT_REQUEST_TIMEOUT,
        auto_start=DEFAULT_AUTO_START,
    )


def build_proxy_router(registry: EmsRegistry, native_backend: Any | None = None) -> Any:
    """Return a FastAPI router (not app) for mounting in an existing app."""
    proxy = EmsProxy(registry, native_backend=native_backend)
    try:
        from fastapi import APIRouter
    except ModuleNotFoundError as exc:
        raise RuntimeError("FastAPI required") from exc

    router = APIRouter(prefix="/v1", tags=["ems"])
    app = proxy.app

    # Copy routes from the standalone app into the router
    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "endpoint"):
            for method in route.methods:
                if method == "HEAD":
                    continue
                router.add_api_route(
                    path=route.path,
                    endpoint=route.endpoint,
                    methods=[method],
                    response_model=getattr(route, "response_model", None),
                )
    return router
