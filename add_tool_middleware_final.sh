#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$HOME/egregore"
MIDDLEWARE_FILE="src/egregore/http_api/http/middleware/tool_call_middleware.py"
BOOTSTRAP_FILE="src/egregore/interface/bootstrap.py"
BACKUP_DIR="backups/tool_middleware_patch"

log()  { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }

cd "$PROJECT_DIR"
[[ -f "$BOOTSTRAP_FILE" ]] || { err "bootstrap.py not found"; exit 1; }

mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

cp "$BOOTSTRAP_FILE" "$BACKUP_DIR/bootstrap.py.$TIMESTAMP.bak"
log "Backup created: $BACKUP_DIR/bootstrap.py.$TIMESTAMP.bak"

MIDDLEWARE_PREEXISTED=0
if [[ -f "$MIDDLEWARE_FILE" ]]; then
  MIDDLEWARE_PREEXISTED=1
  cp "$MIDDLEWARE_FILE" "$BACKUP_DIR/tool_call_middleware.py.$TIMESTAMP.bak"
  log "Backup created: $BACKUP_DIR/tool_call_middleware.py.$TIMESTAMP.bak"
fi

cleanup_on_error() {
  err "Error occurred. Restoring backup..."
  cp "$BACKUP_DIR/bootstrap.py.$TIMESTAMP.bak" "$BOOTSTRAP_FILE"
  if [[ "$MIDDLEWARE_PREEXISTED" -eq 1 ]]; then
    cp "$BACKUP_DIR/tool_call_middleware.py.$TIMESTAMP.bak" "$MIDDLEWARE_FILE"
  else
    rm -f "$MIDDLEWARE_FILE"
  fi
}
trap cleanup_on_error ERR

# Ensure the middleware package directory actually exists before writing to it
mkdir -p "$(dirname "$MIDDLEWARE_FILE")"

cat > "$MIDDLEWARE_FILE" << 'EOF'
"""Prompt-based tool calling for models without native function-calling support.

Intercepts POST /v1/chat/completions requests that include a `tools` array:
  1. On the way in: strips `tools`/`tool_choice`, prepends a system message
     describing the available tools and the expected JSON response shape.
  2. On the way out: scans the assistant's text for a `{"tool_calls": [...]}`
     block and, if found, moves it into `message.tool_calls` (clearing
     `content`) so callers see the same shape native tool calling produces.
"""

import json
from typing import Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def _extract_first_json_object(text: str) -> Optional[dict]:
    """Find the first balanced JSON object in `text`.

    Unlike a greedy `r'\\{.*\\}'` regex, this won't get confused by braces
    that appear before or after the actual tool-call block — it uses the
    JSON decoder's own brace-matching instead of a regex approximation.
    """
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
        if not tools:
            return await call_next(request)

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

        # --- Replace the request's receive channel -------------------------
        # Setting `request._body` alone does NOT propagate downstream:
        # `call_next` passes `request.receive` (a bound method reading the
        # raw ASGI channel) to the downstream app, which builds its OWN
        # fresh Request around that channel. That fresh Request never sees
        # this middleware's cached `_body` — and the original channel is
        # already exhausted from the `await request.body()` call above, so
        # downstream just reads an `http.disconnect`. Overriding `_receive`
        # with a replay callable is the actual fix.
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
        # ---------------------------------------------------------------------

        response = await call_next(request)

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        response_body = b""
        async for chunk in response.body_iterator:
            response_body += chunk

        try:
            resp_payload = json.loads(response_body)
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

        # The body's byte length just changed — headers describing the
        # ORIGINAL body (length, encoding) would make this response invalid.
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("content-encoding", None)
        headers.pop("transfer-encoding", None)
        headers.pop("content-type", None)  # set once via media_type below

        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
        )
EOF

log "Created tool_call_middleware.py"

python3 - "$BOOTSTRAP_FILE" <<'PY'
import sys
import ast
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()
tree = ast.parse(text)

IMPORT_MODULE = "egregore.http_api.http.middleware.tool_call_middleware"
IMPORT_LINE = f"from {IMPORT_MODULE} import ToolCallMiddleware"
REGISTER_CALL = "app.add_middleware(ToolCallMiddleware)"

if REGISTER_CALL in text:
    print("Middleware already registered in bootstrap.py — nothing to do.")
    sys.exit(0)


def find_app_assignment(module: ast.Module):
    # Prefer a module-level `app = FastAPI(...)` / `app = create_app()`.
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "app":
                    return node
    # Fall back to a nested assignment only as a last resort, and say so —
    # ast.walk() order is not guaranteed to match source order, so this
    # heuristic can pick the wrong candidate if there are several.
    for node in ast.walk(module):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "app":
                    print("WARNING: no module-level 'app = ...' found; using a "
                          "nested assignment instead — please verify this is correct.")
                    return node
    return None


app_assignment = find_app_assignment(tree)
if app_assignment is None:
    print("ERROR: could not locate any assignment to 'app' in bootstrap.py", file=sys.stderr)
    sys.exit(1)

already_imported = any(
    isinstance(node, ast.ImportFrom) and node.module == IMPORT_MODULE
    for node in ast.walk(tree)
)

lines = text.splitlines()
import_inserted = False
if not already_imported:
    if lines and lines[0].startswith("#!"):
        lines.insert(1, IMPORT_LINE)
    else:
        lines.insert(0, IMPORT_LINE)
    import_inserted = True

shift = 1 if import_inserted else 0

# .end_lineno (not .lineno) — for a multi-line `app = FastAPI(\n ...\n)` call,
# .lineno is only where the statement STARTS. Inserting there would split
# the statement in half and break the file.
end_line = (app_assignment.end_lineno or app_assignment.lineno) + shift
start_line_idx = app_assignment.lineno - 1 + shift

indent_source = lines[start_line_idx]
indent = len(indent_source) - len(indent_source.lstrip())
indent_str = indent_source[:indent]

lines.insert(end_line, f"{indent_str}{REGISTER_CALL}")
path.write_text("\n".join(lines) + "\n")

print(f"Updated {path}: import {'inserted' if import_inserted else 'already present'}, "
      f"middleware registered after line {end_line}.")
PY

python3 -m py_compile "$MIDDLEWARE_FILE" "$BOOTSTRAP_FILE"
log "✅ Tool calling middleware installed successfully."
