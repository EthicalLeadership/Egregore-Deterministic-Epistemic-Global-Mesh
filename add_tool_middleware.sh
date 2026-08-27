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

# Backup
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
cp "$BOOTSTRAP_FILE" "$BACKUP_DIR/bootstrap.py.$TIMESTAMP.bak"
log "Backup created: $BACKUP_DIR/bootstrap.py.$TIMESTAMP.bak"

trap 'err "Error occurred. Restoring backup..."; cp "$BACKUP_DIR/bootstrap.py.$TIMESTAMP.bak" "$BOOTSTRAP_FILE";' ERR

# Create the middleware file
cat > "$MIDDLEWARE_FILE" << 'EOF'
import json
import re
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

class ToolCallMiddleware(BaseHTTPMiddleware):
    """Intercept requests containing 'tools' and add prompt-based tool calling.

    If the request body contains a 'tools' array, we:
      1. Prepend a system message that instructs the model to output a JSON tool_calls block when needed.
      2. After the response is generated, parse the assistant's text for that JSON block.
      3. Set the parsed tool_calls on the response message, and clear the content.
    """

    async def dispatch(self, request: Request, call_next):
        # Only intercept POST /v1/chat/completions
        if request.url.path != "/v1/chat/completions" or request.method != "POST":
            return await call_next(request)

        # Read the body (requires bytes)
        body = await request.body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return await call_next(request)

        tools = payload.get("tools")
        if not tools:
            # No tools, just pass through
            return await call_next(request)

        # Build tool prompt
        tool_prompt = "You have access to the following tools:\n"
        for tool in tools:
            func = tool.get("function", {})
            name = func.get("name", "")
            desc = func.get("description", "")
            tool_prompt += f"- {name}: {desc}\n"
        tool_prompt += "\nIf you need to use a tool, respond with ONLY a JSON object in this format:\n"
        tool_prompt += '{"tool_calls": [{"name": "tool_name", "arguments": {arg1: value1, ...}}]}\n'
        tool_prompt += "Otherwise, respond normally with your message."

        # Prepend system message
        messages = payload.get("messages", [])
        payload["messages"] = [{"role": "system", "content": tool_prompt}] + messages

        # Remove tools from payload so the underlying model doesn't get confused
        payload.pop("tools", None)
        payload.pop("tool_choice", None)

        # Reconstruct request body
        import io
        new_body = json.dumps(payload).encode("utf-8")
        request._body = new_body
        request.headers["content-length"] = str(len(new_body))

        # Continue with modified request
        response = await call_next(request)

        # If response is JSON and contains choices, try to extract tool_calls
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            response_body = b""
            async for chunk in response.body_iterator:
                response_body += chunk
            try:
                resp_payload = json.loads(response_body)
                choices = resp_payload.get("choices", [])
                if choices:
                    first_choice = choices[0]
                    message = first_choice.get("message", {})
                    content = message.get("content")
                    if content:
                        # Search for JSON block
                        json_match = re.search(r'\{.*\}', content, re.DOTALL)
                        if json_match:
                            try:
                                tool_data = json.loads(json_match.group(0))
                                if 'tool_calls' in tool_data:
                                    message['tool_calls'] = tool_data['tool_calls']
                                    message['content'] = None
                                    first_choice['message'] = message
                                    choices[0] = first_choice
                                    resp_payload['choices'] = choices
                                    response_body = json.dumps(resp_payload).encode("utf-8")
                            except json.JSONDecodeError:
                                pass
            except json.JSONDecodeError:
                pass

            # Create new response with modified body
            return Response(
                content=response_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type="application/json"
            )

        return response
EOF

log "Created tool_call_middleware.py"

# Modify bootstrap.py to register the middleware
python - "$BOOTSTRAP_FILE" <<'PY'
import sys, ast
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()
tree = ast.parse(text)

# Find the create_app function
create_app = None
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == "create_app":
        create_app = node
        break
if create_app is None:
    # Maybe it's a factory returning app; look for app = FastAPI()
    # We'll search for any assignment to FastAPI
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == "FastAPI":
                create_app = node
                break
if create_app is None:
    print("ERROR: could not locate app creation in bootstrap.py")
    sys.exit(1)

# Find a good place to insert: after app = FastAPI(...) or within create_app before return
insert_line = create_app.lineno
if hasattr(create_app, 'end_lineno'):
    insert_line = create_app.end_lineno  # after the function definition? Not ideal; we want before return
    # Better: find the 'return app' statement inside create_app
    for sub in ast.walk(create_app):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Name) and sub.value.id == "app":
            insert_line = sub.lineno
            break

# Check if import already present
if "tool_call_middleware" not in text:
    # Add import at top
    import_line = "from egregore.http_api.http.middleware.tool_call_middleware import ToolCallMiddleware\n"
    lines = text.splitlines()
    # Insert after last import? We'll just add at the very top
    lines.insert(0, import_line)
    text = "\n".join(lines)

# Add middleware registration
if "add_middleware(ToolCallMiddleware)" not in text:
    reg_line = "    app.add_middleware(ToolCallMiddleware)\n"
    lines = text.splitlines()
    # Insert before the 'return app' line (which is at insert_line, 1-based)
    idx = None
    for i, line in enumerate(lines):
        if i + 1 == insert_line:
            idx = i
            break
    if idx is None:
        print("ERROR: could not locate insertion point for middleware registration")
        sys.exit(1)
    lines.insert(idx, reg_line)
    text = "\n".join(lines)

path.write_text(text)
print(f"Updated {path} with middleware registration")
PY

# Validate syntax
python -m py_compile "$MIDDLEWARE_FILE" "$BOOTSTRAP_FILE"
log "✅ Tool calling middleware installed successfully."
