"""Middleware that adapts Egregore chat API to OpenAI-compatible format.

- On request: if `tools` are provided, inject a system prompt and strip `tools`.
- On response: convert Egregore's flat `message` object into the standard
  OpenAI `choices` array, and ensure tool_calls are present when applicable.
"""

import json
import time
from typing import Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def _extract_first_json_object(text: str) -> Optional[dict]:
    """Find the first balanced JSON object in `text`."""
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text, start)
            return obj
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    return None


class ToolCallMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/v1/chat/completions" or request.method != "POST":
            return await call_next(request)

        body = await request.body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return await call_next(request)

        tools = payload.get("tools")
        if tools:
            # Build tool prompt and modify request
            tool_prompt = "You have access to the following tools:\n"
            for tool in tools:
                func = tool.get("function", {})
                name = func.get("name", "")
                desc = func.get("description", "")
                tool_prompt += f"- {name}: {desc}\n"
            tool_prompt += (
                "\nIf you need to use a tool, respond with ONLY a JSON object in "
                'this format:\n{"tool_calls": [{"name": "tool_name", "arguments": '
                "{arg1: value1, ...}}]}\n"
                "Otherwise, respond normally with your message."
            )
            messages = payload.get("messages", [])
            payload["messages"] = [{"role": "system", "content": tool_prompt}] + messages
            payload.pop("tools", None)
            payload.pop("tool_choice", None)

            new_body = json.dumps(payload).encode("utf-8")
            body_sent = False
            async def receive() -> dict[str, Any]:
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return {"type": "http.request", "body": new_body, "more_body": False}
                return {"type": "http.disconnect"}

            request._receive = receive
            request._stream_consumed = False
            if hasattr(request, "_body"):
                del request._body
            request.scope["headers"] = [
                (k, v) for k, v in request.scope["headers"]
                if k.lower() != b"content-length"
            ]

        response = await call_next(request)

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        response_body = b""
        async for chunk in response.body_iterator:
            response_body += chunk

        try:
            resp_payload = json.loads(response_body)

            # If response already has 'choices', assume it's already OpenAI-shaped
            if "choices" not in resp_payload:
                # Convert flat format to OpenAI format
                message = resp_payload.get("message")
                if message:
                    # Extract tool_calls from content if needed
                    content = message.get("content")
                    if content and tools:
                        tool_data = _extract_first_json_object(content)
                        if tool_data and "tool_calls" in tool_data:
                            message["tool_calls"] = tool_data["tool_calls"]
                            message["content"] = None

                    choices = [{
                        "index": 0,
                        "message": message,
                        "finish_reason": resp_payload.get("finish_reason", "stop")
                    }]
                    openai_payload = {
                        "id": resp_payload.get("id") or f"chatcmpl-{int(time.time())}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": resp_payload.get("model"),
                        "choices": choices,
                        "usage": resp_payload.get("usage", {})
                    }
                    response_body = json.dumps(openai_payload).encode("utf-8")
                else:
                    # No message field? leave as is
                    pass
            else:
                # Already has choices; still ensure tool_calls extraction if needed
                if tools:
                    choices = resp_payload.get("choices", [])
                    if choices:
                        message = choices[0].get("message", {})
                        content = message.get("content")
                        if content:
                            tool_data = _extract_first_json_object(content)
                            if tool_data and "tool_calls" in tool_data:
                                message["tool_calls"] = tool_data["tool_calls"]
                                message["content"] = None
                                choices[0]["message"] = message
                                resp_payload["choices"] = choices
                                response_body = json.dumps(resp_payload).encode("utf-8")
        except json.JSONDecodeError:
            pass

        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("content-encoding", None)
        headers.pop("transfer-encoding", None)
        headers.pop("content-type", None)

        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
        )
