#!/usr/bin/env python3
"""
Egregore OpenAI-Compatible API Server
Exposes /v1/chat/completions so VS Code extensions can use Egregore as a local AI provider.
"""

import os
import json
import time
import uuid
import asyncio
from typing import List, Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

# ============================
# Configuration
# ============================
API_KEY = os.environ.get("EGREGORE_API_KEY", "sk-egregore-local")
HOST = "127.0.0.1"
PORT = 8000

# ============================
# Egregore Orchestrator Interface
# ============================
async def call_egregore_orchestrator(
    messages: List[Dict[str, str]],
    model: str,
    temperature: float = 0.0,
    seed: int = 42,
    max_tokens: int = 2048
) -> str:
    """
    This is the bridge to your existing Egregore orchestrator.
    Replace this function with your actual call.

    The orchestrator should:
      1. Receive the chat history (messages).
      2. Apply Blackstar deterministic policy (temperature=0, fixed seed).
      3. Route to the appropriate local model (DeepSeek, Kimi, etc.).
      4. Return the assistant's reply as a string.

    For now, we provide a stub that returns a deterministic echo,
    but you MUST replace it with your real logic.
    """
    # Example of how you might call your orchestrator:
    # from egregore.application.orchestrator import process_chat
    # return await process_chat(messages, model, temperature, seed, max_tokens)

    # Temporary stub:
    last_user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return f"[Egregore Orchestrator] You said: {last_user_msg}"


# ============================
# FastAPI Application
# ============================
app = FastAPI(title="Egregore OpenAI-Compatible API")

def verify_api_key(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth.split(" ")[1]
    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    verify_api_key(request)
    data = await request.json()

    messages = data.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    model = data.get("model", "egregore-default")
    temperature = float(data.get("temperature", 0.0))
    seed = int(data.get("seed", 42))
    max_tokens = int(data.get("max_tokens", 2048))

    # Call Egregore (this is where the real orchestration happens)
    response_text = await call_egregore_orchestrator(
        messages=messages,
        model=model,
        temperature=temperature,
        seed=seed,
        max_tokens=max_tokens
    )

    # Build OpenAI-compatible response object
    response = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }
    }
    return JSONResponse(response)


# Optional streaming endpoint (needed by some extensions)
@app.post("/v1/chat/completions/stream")
async def chat_completions_stream(request: Request):
    verify_api_key(request)
    data = await request.json()

    messages = data.get("messages", [])
    model = data.get("model", "egregore-default")
    temperature = float(data.get("temperature", 0.0))
    seed = int(data.get("seed", 42))
    max_tokens = int(data.get("max_tokens", 2048))

    # Simulate streaming by yielding chunks of the response
    response_text = await call_egregore_orchestrator(
        messages=messages,
        model=model,
        temperature=temperature,
        seed=seed,
        max_tokens=max_tokens
    )

    async def event_generator():
        # Send chunks of the text as SSE events
        chunk_size = 10
        for i in range(0, len(response_text), chunk_size):
            chunk = response_text[i:i+chunk_size]
            event = {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": chunk},
                        "finish_reason": None
                    }
                ]
            }
            yield f"data: {json.dumps(event)}\n\n"
            await asyncio.sleep(0.01)
        # Send final chunk with finish_reason
        event = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop"
                }
            ]
        }
        yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
