#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$HOME/egregore"
CHAT_FILE="src/egregore/http_api/http/v1/chat.py"
BACKUP_DIR="backups/tool_calling_patch"

log()  { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }

cd "$PROJECT_DIR"
[[ -f "$CHAT_FILE" ]] || { err "File not found: $CHAT_FILE"; exit 1; }

# Backup
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
cp "$CHAT_FILE" "$BACKUP_DIR/chat.py.$TIMESTAMP.bak"
log "Backup created: $BACKUP_DIR/chat.py.$TIMESTAMP.bak"

trap 'err "Error occurred. Restoring backup..."; cp "$BACKUP_DIR/chat.py.$TIMESTAMP.bak" "$CHAT_FILE";' ERR

python - "$CHAT_FILE" <<'PY'
import sys, ast, re
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()

# Check if the post-processing block already exists (idempotency)
if "http_response = _to_http_response(response)" in text:
    print("Tool calling post-processing already present. Skipping all changes.")
    sys.exit(0)

tree = ast.parse(text)

# --------------------------------------------------------------------------
# 1. Locate classes
# --------------------------------------------------------------------------
request_class = response_class = message_schema_class = None
for node in tree.body:
    if isinstance(node, ast.ClassDef):
        if node.name == "ChatCompletionRequest":
            request_class = node
        elif node.name == "ChatCompletionResponse":
            response_class = node
        elif node.name == "ChatMessageSchema":
            message_schema_class = node

if request_class is None or response_class is None:
    print("ERROR: ChatCompletionRequest or ChatCompletionResponse not found")
    sys.exit(1)

# --------------------------------------------------------------------------
# 2. Add fields to ChatCompletionRequest
# --------------------------------------------------------------------------
messages_ann = next(
    (n for n in request_class.body if isinstance(n, ast.AnnAssign)
     and isinstance(n.target, ast.Name) and n.target.id == "messages"),
    None
)
if messages_ann is None:
    print("ERROR: 'messages' not found in ChatCompletionRequest")
    sys.exit(1)

insert_line_req = messages_ann.end_lineno
existing_req = {n.target.id for n in request_class.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}

if "tools" not in existing_req or "tool_choice" not in existing_req:
    lines = text.splitlines()
    additions = []
    if "tools" not in existing_req:
        additions.append("    tools: list[dict] | None = None")
    if "tool_choice" not in existing_req:
        additions.append("    tool_choice: str | dict | None = None")
    idx = None
    for i, line in enumerate(lines):
        if i + 1 == insert_line_req:
            idx = i + 1
            break
    if idx is None:
        print("ERROR: cannot locate insertion point for request fields")
        sys.exit(1)
    for j, add in enumerate(additions):
        lines.insert(idx + j, add)
    text = "\n".join(lines)
    # Re-parse for later steps
    tree = ast.parse(text)
    request_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChatCompletionRequest")
    response_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChatCompletionResponse")
    message_schema_class = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChatMessageSchema"), None)
else:
    print("Request fields already present.")

# --------------------------------------------------------------------------
# 3. Add tool_calls to ChatMessageSchema (if class exists)
# --------------------------------------------------------------------------
if message_schema_class is not None:
    existing_schema = {n.target.id for n in message_schema_class.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    if "tool_calls" not in existing_schema:
        content_ann = next(
            (n for n in message_schema_class.body if isinstance(n, ast.AnnAssign)
             and isinstance(n.target, ast.Name) and n.target.id == "content"),
            None
        )
        if content_ann is not None:
            insert_line_schema = content_ann.end_lineno
        else:
            # If no content field, insert after class definition line
            insert_line_schema = message_schema_class.lineno  # this is line of class statement, not safe
            # Better: after the first statement (usually a docstring or first field)
            if message_schema_class.body:
                insert_line_schema = message_schema_class.body[0].lineno
            else:
                insert_line_schema = message_schema_class.lineno + 1
        lines = text.splitlines()
        new_line = "    tool_calls: list[dict] | None = None"
        idx_schema = None
        for i, line in enumerate(lines):
            if i + 1 == insert_line_schema:
                idx_schema = i + 1
                break
        if idx_schema is None:
            print("ERROR: cannot find insertion point for tool_calls in ChatMessageSchema")
            sys.exit(1)
        lines.insert(idx_schema, new_line)
        text = "\n".join(lines)
        print("Added tool_calls to ChatMessageSchema")
    else:
        print("ChatMessageSchema already has tool_calls")
else:
    print("ChatMessageSchema not found; tool_calls will be set dynamically.")

# --------------------------------------------------------------------------
# 4. Add tool_calls to ChatCompletionResponse (optional)
# --------------------------------------------------------------------------
# Re-parse after possible changes
tree = ast.parse(text)
response_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChatCompletionResponse")
existing_resp = {n.target.id for n in response_class.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
if "tool_calls" not in existing_resp:
    message_ann = next(
        (n for n in response_class.body if isinstance(n, ast.AnnAssign)
         and isinstance(n.target, ast.Name) and n.target.id == "message"),
        None
    )
    if message_ann is not None:
        insert_line_resp = message_ann.end_lineno
    else:
        insert_line_resp = response_class.lineno + 1
    lines = text.splitlines()
    new_line = "    tool_calls: list[dict] | None = None"
    idx_resp = None
    for i, line in enumerate(lines):
        if i + 1 == insert_line_resp:
            idx_resp = i + 1
            break
    if idx_resp is None:
        print("ERROR: cannot locate insertion point for tool_calls in ChatCompletionResponse")
        sys.exit(1)
    lines.insert(idx_resp, new_line)
    text = "\n".join(lines)
    print("Added tool_calls to ChatCompletionResponse")

# --------------------------------------------------------------------------
# 5. Insert tool prompt block before model check
# --------------------------------------------------------------------------
tree = ast.parse(text)
model_check = None
for n in ast.walk(tree):
    if isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp) and isinstance(n.test.op, ast.Not) \
            and isinstance(n.test.operand, ast.Call) and isinstance(n.test.operand.func, ast.Attribute) \
            and n.test.operand.func.attr == "model_exists":
        model_check = n
        break
if model_check is None:
    print("ERROR: could not locate 'if not service.model_exists(req.model):'")
    sys.exit(1)
insert_before = model_check.lineno

if "# If tools are provided" not in text:
    block = '''\
    # If tools are provided, use prompt-based tool calling
    if req.tools:
        tool_prompt = "You have access to the following tools:\\n"
        for tool in req.tools:
            tool_prompt += f"- {tool['function']['name']}: {tool['function'].get('description','')}\\n"
        tool_prompt += "\\nIf you need to use a tool, respond with ONLY a JSON object in this format:\\n"
        tool_prompt += '{"tool_calls": [{"name": "tool_name", "arguments": {arg1: value1, ...}}]}\\n'
        tool_prompt += "Otherwise, respond normally with your message."

        # Prepend as system message
        system_msg = {"role": "system", "content": tool_prompt}
        messages = [system_msg] + req.messages

        # Reconstruct request preserving all fields except tools/tool_choice
        if hasattr(req, "model_dump"):
            req_dict = req.model_dump(exclude={"tools", "tool_choice"})
        else:
            req_dict = req.dict(exclude={"tools", "tool_choice"})
        req_dict["messages"] = messages
        req = ChatCompletionRequest(**req_dict)

'''
    lines = text.splitlines()
    insert_idx = insert_before - 1
    block_lines = block.splitlines() + ['']  # ensure trailing empty line
    lines = lines[:insert_idx] + block_lines + lines[insert_idx:]
    text = "\n".join(lines)
else:
    print("Tool prompt block already present.")

# --------------------------------------------------------------------------
# 6. Replace return statement with assignment + post-processing
# --------------------------------------------------------------------------
pattern = re.compile(r'(\s*)return\s+_to_http_response\(response\)\s*\n?')
match = pattern.search(text)
if match is None:
    print("ERROR: could not find 'return _to_http_response(response)'")
    sys.exit(1)

indent = match.group(1) or "    "
replacement = f'''{indent}http_response = _to_http_response(response)
{indent}if req.tools:
{indent}    import json, re
{indent}    content = http_response.choices[0].message.content
{indent}    json_match = re.search(r'\\{{.*\\}}', content, re.DOTALL)
{indent}    if json_match:
{indent}        try:
{indent}            tool_data = json.loads(json_match.group(0))
{indent}            if 'tool_calls' in tool_data:
{indent}                http_response.choices[0].message.tool_calls = tool_data['tool_calls']
{indent}                http_response.choices[0].message.content = None
{indent}        except json.JSONDecodeError:
{indent}            pass
{indent}return http_response
'''

# Replace the matched line (including optional newline) with the replacement text
text = text[:match.start()] + replacement + text[match.end():]

# --------------------------------------------------------------------------
# Write final file
# --------------------------------------------------------------------------
path.write_text(text)
print(f"Updated {path} successfully.")
PY

# Validate syntax
python -m py_compile "$CHAT_FILE"
log "✅ Tool calling support added successfully."	
