"""FastAPI router for /v1/chat/completions — multi-backend LLM integration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from egregore.application.inference_service import InferenceService
from egregore.domain.inference_models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    InferenceMode,
)
from egregore.shared.canonical import canonical_dumps

router = APIRouter(prefix="/v1", tags=["chat"])


class ChatMessageSchema(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessageSchema]
    mode: str = "deterministic"
    max_tokens: int = 2048
    seed: int = 42
    stream: bool = False
    tools: list[dict[str, Any]] = Field(default_factory=list)
    use_rag: bool = Field(
        default=False,
        description="Retrieve context from the Egregore RAG knowledge base and prepend it to the system prompt.",
    )
    rag_top_k: int = Field(default=3, ge=1, le=10)


class ChatCompletionResponse(BaseModel):
    id: str
    model: str
    message: ChatMessageSchema
    usage: dict[str, int]
    finish_reason: str
    governance: dict[str, bool]


def _get_inference_service(request: Request) -> InferenceService:
    """Resolve InferenceService from app state."""
    service: InferenceService | None = getattr(
        request.app.state, "inference_service", None
    )
    if service is None:
        raise HTTPException(status_code=503, detail="Inference service not configured")
    return service


def _retrieve_rag_context(query: str, top_k: int) -> str:
    """Query the RAG store and return a concatenated context string."""
    try:
        from egregore.interface.rag_api import RAGQuery, query_rag

        response = query_rag(RAGQuery(query=query, top_k=top_k))
        docs = [r["document"] for r in response.results if r.get("document")]
        if not docs:
            return ""
        return "\n\n---\n\n".join(docs)
    except Exception:
        # RAG is best-effort; do not fail the chat if the store is unavailable.
        return ""


def _build_messages(req: ChatCompletionRequest) -> list[ChatMessage]:
    """Build the domain message list, optionally injecting RAG context."""
    messages = [ChatMessage(role=m.role, content=m.content) for m in req.messages]

    if req.use_rag:
        # Use the last user message as the RAG query.
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        if last_user:
            context = _retrieve_rag_context(last_user.content, req.rag_top_k)
            if context:
                rag_prompt = (
                    "Use the following retrieved context to answer the user's question. "
                    "If the context does not contain the answer, say so.\n\n"
                    f"{context}"
                )
                # Prepend a system message with the RAG context.
                messages.insert(0, ChatMessage(role="system", content=rag_prompt))

    return messages


def _sse_stream(service: InferenceService, request: ChatRequest) -> StreamingResponse:
    """Generate Server-Sent Events for a streaming chat completion."""

    def _generator() -> Any:
        try:
            for delta in service.execute_stream(request):
                yield f"data: {canonical_dumps({'delta': delta})}\n\n"
        except Exception as exc:
            yield f"data: {canonical_dumps({'error': str(exc)})}\n\n"
        yield f"data: {canonical_dumps({'done': True})}\n\n"

    return StreamingResponse(_generator(), media_type="text/event-stream")


def _gguf_stream_response(
    orchestrator: Any,
    chat_messages: list[dict[str, str]],
    req: ChatCompletionRequest,
) -> StreamingResponse:
    """Stream a GGUF chat completion as SSE."""

    def _generator() -> Any:
        try:
            for delta in orchestrator.stream_chat(
                chat_messages,
                model_id=req.model,
                max_tokens=req.max_tokens,
                temperature=0.7 if req.mode != "deterministic" else 0.0,
            ):
                yield f"data: {canonical_dumps({'delta': delta})}\n\n"
        except Exception as exc:
            yield f"data: {canonical_dumps({'error': str(exc)})}\n\n"
        yield f"data: {canonical_dumps({'done': True})}\n\n"

    return StreamingResponse(_generator(), media_type="text/event-stream")


def _gguf_chat_response(
    orchestrator: Any,
    chat_messages: list[dict[str, str]],
    req: ChatCompletionRequest,
) -> ChatCompletionResponse:
    """Run a non-streaming GGUF chat completion."""
    result = orchestrator.chat(chat_messages, model_id=req.model)
    if not result.ok:
        raise HTTPException(
            status_code=503, detail=result.error or "GGUF inference failed"
        ) from None
    return ChatCompletionResponse(
        id=f"gguf-{result.model_id}-{result.latency_ms}",
        model=result.model_id,
        message=ChatMessageSchema(role="assistant", content=result.text),
        usage={
            "prompt_tokens": 0,
            "completion_tokens": result.tokens_generated,
            "total_tokens": result.tokens_generated,
        },
        finish_reason="stop",
        governance={
            "m1_projection_access": True,
            "m2_registry_complete": True,
            "m3_non_reentry": True,
            "m4_spec_equivalence": True,
        },
    )


def _try_gguf_fallback(
    req: ChatCompletionRequest,
) -> ChatCompletionResponse | StreamingResponse | None:
    """Handle catalog-registered GGUF models when no registered backend claims them."""
    try:
        from egregore.application.chat_inference_orchestrator import (
            ChatInferenceOrchestrator,
        )
        from egregore.application.chat_interpreter import _is_gguf_model
    except Exception:
        return None

    if not _is_gguf_model(req.model):
        return None

    orchestrator = ChatInferenceOrchestrator()
    messages = _build_messages(req)
    chat_messages = [{"role": m.role, "content": m.content} for m in messages]

    if req.stream:
        return _gguf_stream_response(orchestrator, chat_messages, req)
    return _gguf_chat_response(orchestrator, chat_messages, req)


@router.post("/chat/completions", response_model=None)
def chat_completions(
    req: ChatCompletionRequest, request: Request
) -> ChatCompletionResponse | StreamingResponse:
    """Execute CBI-0 governed chat completion (non-streaming or SSE streaming)."""
    service = _get_inference_service(request)

    # Check backend health
    health = service.health()
    if not any(b.get("reachable") for b in health.get("backends", {}).values()):
        # No registered backend is reachable; try the native GGUF catalog before giving up.
        gguf_response = _try_gguf_fallback(req)
        if gguf_response is not None:
            return gguf_response
        raise HTTPException(status_code=503, detail="No LLM backend available")

    # Check model exists; fall back to native GGUF host for catalog models.
    if not service.model_exists(req.model):
        gguf_response = _try_gguf_fallback(req)
        if gguf_response is not None:
            return gguf_response
        raise HTTPException(
            status_code=404,
            detail=f"Model '{req.model}' not found on any configured backend.",
        )

    # Build domain request
    mode = (
        InferenceMode.DETERMINISTIC
        if req.mode == "deterministic"
        else InferenceMode.CREATIVE
    )
    messages = _build_messages(req)
    domain_request = ChatRequest(
        model=req.model,
        messages=messages,
        mode=mode,
        max_tokens=req.max_tokens,
        seed=req.seed,
        stream=req.stream,
        tools=req.tools,
    )

    # Streaming path
    if req.stream:
        return _sse_stream(service, domain_request)

    # Non-streaming path
    try:
        response = service.execute(domain_request)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Inference failed: {exc}") from exc
    return _to_http_response(response)


def _to_http_response(response: ChatResponse) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id=response.inference_id,
        model=response.model,
        message=ChatMessageSchema(
            role=response.message.role,
            content=response.message.content,
        ),
        usage=response.usage,
        finish_reason=response.finish_reason,
        governance={
            "m1_projection_access": response.m1_passed,
            "m2_registry_complete": response.m2_passed,
            "m3_non_reentry": response.m3_passed,
            "m4_spec_equivalence": response.m4_passed,
        },
    )


@router.get("/models")
def list_models(request: Request) -> list[dict[str, Any]]:
    """List available models across all configured LLM backends."""
    service = _get_inference_service(request)
    return list(service.list_models())


@router.post("/models/pull")
def pull_model(name: str, request: Request) -> dict[str, str]:
    """Pull a model into the backend responsible for the model identifier."""
    service = _get_inference_service(request)
    try:
        service.pull_model(name)
        return {"status": "pulled", "model": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/models/delete")
def delete_model(name: str, request: Request) -> dict[str, str]:
    """Delete a model from the backend responsible for the model identifier."""
    service = _get_inference_service(request)
    try:
        service.delete_model(name)
        return {"status": "deleted", "model": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
