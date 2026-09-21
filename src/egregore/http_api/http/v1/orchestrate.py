"""FastAPI router for /v1/orchestrate — manifest-driven model auto-selection.

Routes a chat request to the best available GGUF model for a task type using
the deterministic ModelSelector (catalog mirror + model_profiles.json), then
executes through the governed InferenceService (M1-M4).

Security:
- Auth is enforced globally by APIKeyMiddleware (same as /v1/chat).
- Internal resolution details (catalog keys, reasons) are only disclosed when
  ORCHESTRATE_DISCLOSE_INTERNAL=true.
- Streaming checks client disconnect and enforces a global timeout.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from egregore.application.inference_service import InferenceService
from egregore.application.model_selector import (
    ALLOWED_TASKS,
    ModelSelector,
    NoModelAvailableError,
    SelectionResult,
    load_profiles,
)
from egregore.domain.inference_models import (
    ChatMessage,
    ChatRequest,
    InferenceMode,
)
from egregore.http_api.http.middleware.inference_metrics import INFERENCE_METRICS
from egregore.shared.canonical import canonical_dumps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/orchestrate", tags=["orchestrate"])

_CACHE_TTL_S = float(os.environ.get("ORCHESTRATE_CACHE_TTL_S", "300"))
_STREAM_TIMEOUT_S = float(os.environ.get("ORCHESTRATE_STREAM_TIMEOUT_S", "120"))
_SENTINEL = object()

_selector_cache: tuple[float, ModelSelector] | None = None


class ChatMessageSchema(BaseModel):
    role: str
    content: str


class OrchestrateRequest(BaseModel):
    messages: list[ChatMessageSchema]
    task_type: str = "general"
    model: str | None = None  # explicit override; bypasses the selector
    mode: str = "deterministic"
    max_tokens: int = 2048
    seed: int = 42
    stream: bool = False


def _disclose_internal() -> bool:
    return os.environ.get("ORCHESTRATE_DISCLOSE_INTERNAL", "false").lower() in (
        "1",
        "true",
        "yes",
    )


def _get_selector() -> ModelSelector:
    """Catalog + profiles cache with TTL."""
    global _selector_cache
    now = time.monotonic()
    if _selector_cache is not None and now - _selector_cache[0] < _CACHE_TTL_S:
        return _selector_cache[1]
    from egregore.infrastructure.gguf_catalog import GGUFCatalog

    selector = ModelSelector(GGUFCatalog().get_catalog(), load_profiles())
    _selector_cache = (now, selector)
    return selector


def _reset_cache() -> None:
    """Test hook: drop the cached selector."""
    global _selector_cache
    _selector_cache = None


def _get_inference_service(request: Request) -> InferenceService:
    service: InferenceService | None = getattr(
        request.app.state, "inference_service", None
    )
    if service is None:
        raise HTTPException(status_code=503, detail="Inference service not configured")
    return service


def _resolve(req: OrchestrateRequest) -> SelectionResult:
    """Pick the execution model, honouring the explicit override."""
    if req.model:
        return SelectionResult(
            logical_id=req.model,
            routes_to=req.model,
            catalog_key="",
            reason="explicit model override",
            fallback_used=False,
        )
    if req.task_type not in ALLOWED_TASKS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid task_type: {req.task_type}. Allowed: {list(ALLOWED_TASKS)}",
        )
    try:
        return _get_selector().select(req.task_type)
    except NoModelAvailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        # Malformed profiles manifest — fail closed.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _execution_model(resolution: SelectionResult, explicit: bool) -> str:
    """Backend-routable model name for InferenceService."""
    if explicit:
        return resolution.routes_to
    # Force the GGUF backend regardless of the default-backend regime.
    return f"gguf-{resolution.routes_to}"


def _resolution_block(resolution: SelectionResult) -> dict[str, Any]:
    if _disclose_internal():
        return {
            "task_type": None,  # filled by caller
            "selected_model": resolution.routes_to,
            "logical_id": resolution.logical_id,
            "catalog_key": resolution.catalog_key,
            "fallback_used": resolution.fallback_used,
            "reason": resolution.reason,
        }
    return {
        "task_type": None,  # filled by caller
        "selected_model": resolution.routes_to,
        "fallback_used": resolution.fallback_used,
        "reason": "selected by task profile",
    }


def _build_domain_request(req: OrchestrateRequest, model: str) -> ChatRequest:
    mode = (
        InferenceMode.DETERMINISTIC
        if req.mode == "deterministic"
        else InferenceMode.CREATIVE
    )
    return ChatRequest(
        model=model,
        messages=[ChatMessage(role=m.role, content=m.content) for m in req.messages],
        mode=mode,
        max_tokens=req.max_tokens,
        seed=req.seed,
        stream=req.stream,
    )


async def _sse_generator(
    service: InferenceService,
    chat_req: ChatRequest,
    resolution_block: dict[str, Any],
    request: Request,
) -> Any:
    start = time.monotonic()
    loop = asyncio.get_running_loop()
    yield f"data: {canonical_dumps({'resolution': resolution_block})}\n\n"
    try:
        iterator = iter(service.execute_stream(chat_req))
        while True:
            if await request.is_disconnected():
                logger.info("orchestrate stream: client disconnected")
                break
            if time.monotonic() - start > _STREAM_TIMEOUT_S:
                yield f"data: {canonical_dumps({'error': 'stream timeout'})}\n\n"
                break
            delta = await loop.run_in_executor(
                None, functools.partial(next, iterator, _SENTINEL)
            )
            if delta is _SENTINEL:
                break
            yield f"data: {canonical_dumps({'delta': delta})}\n\n"
    except Exception as exc:  # noqa: BLE001
        yield f"data: {canonical_dumps({'error': str(exc)})}\n\n"
    finally:
        INFERENCE_METRICS.record(
            latency_ms=(time.monotonic() - start) * 1000,
            prompt_tokens=0,
            completion_tokens=0,
            error=False,
        )
    yield f"data: {canonical_dumps({'done': True})}\n\n"


@router.post("/chat/completions", response_model=None)
async def chat_completions(req: OrchestrateRequest, request: Request) -> Any:
    """Auto-select a model for the task type and run a governed completion."""
    service = _get_inference_service(request)
    resolution = _resolve(req)
    resolution_block = _resolution_block(resolution)
    resolution_block["task_type"] = req.task_type
    chat_req = _build_domain_request(
        req, _execution_model(resolution, explicit=bool(req.model))
    )

    if req.stream:
        return StreamingResponse(
            _sse_generator(service, chat_req, resolution_block, request),
            media_type="text/event-stream",
        )

    loop = asyncio.get_running_loop()
    start = time.perf_counter()
    error = False
    response = None
    try:
        response = await loop.run_in_executor(None, service.execute, chat_req)
    except RuntimeError as exc:
        error = True
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        error = True
        raise HTTPException(status_code=500, detail=f"Inference failed: {exc}") from exc
    finally:
        latency_ms = (time.perf_counter() - start) * 1000
        usage = response.usage if response else {}
        INFERENCE_METRICS.record(
            latency_ms=latency_ms,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            error=error,
        )

    return {
        "id": response.inference_id,
        "model": response.model,
        "message": {
            "role": response.message.role,
            "content": response.message.content,
        },
        "usage": response.usage,
        "finish_reason": response.finish_reason,
        "governance": {
            "m1_projection_access": response.m1_passed,
            "m2_registry_complete": response.m2_passed,
            "m3_non_reentry": response.m3_passed,
            "m4_spec_equivalence": response.m4_passed,
        },
        "resolution": resolution_block,
    }
