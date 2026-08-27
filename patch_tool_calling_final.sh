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

# Trap to restore on error
trap 'err "Error occurred. Restoring backup..."; cp "$BACKUP_DIR/chat.py.$TIMESTAMP.bak" "$CHAT_FILE";' ERR

python - "$CHAT_FILE" <<'PY'
import sys, ast, re
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()
tree = ast.parse(text)

# Locate classes
request_class = None
response_class = None
for node in tree.body:
    if isinstance(node, ast.ClassDef):
        if node.name == "ChatCompletionRequest":
            request_class = node
        elif node.name == "ChatCompletionResponse":
            response_class = node

if request_class is None or response_class is None:
    print("ERROR: Could not find ChatCompletionRequest or ChatCompletionResponse in chat.py")
    sys.exit(1)

# --- 1. Add fields to ChatCompletionRequest ---
messages_ann = None
for n in request_class.body:
    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "messages":
        messages_ann = n
        break
if messages_ann is None:
    print("ERROR: 'messages' annotation not found in ChatCompletionRequest")
    sys.exit(1)
insert_line_req = messages_ann.end_lineno  # 1-based line number

existing_req = {n.target.id for n in request_class.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
if "tools" not in existing_req or "tool_choice" not in existing_req:
    lines = text.splitlines()
    additions = []
    if "tools" not in existing_req:
        additions.append("    tools: list[dict] | None = None")
    if "tool_choice" not in existing_req:
        additions.append("    tool_choice: str | dict | None = None")
    # Insert after the messages line (1-based line number)
    idx_req = None
    for i, line in enumerate(lines):
        if i + 1 == insert_line_req:
            idx_req = i + 1  # insert after this line
            break
    if idx_req is None:
        print("ERROR: cannot find insertion point for request fields")
        sys.exit(1)
    for j, add in enumerate(additions):
        lines.insert(idx_req + j, add)
    text = "\n".join(lines)
    # Re-parse for next step
    tree = ast.parse(text)
    request_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChatCompletionRequest")
    response_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChatCompletionResponse")
else:
    print("Request fields already present, skipping.")

# --- 2. Add field to ChatCompletionResponse ---
message_ann = None
for n in response_class.body:
    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "message":
        message_ann = n
        break
if message_ann is None:
    print("ERROR: 'message' not found in ChatCompletionResponse")
    sys.exit(1)
insert_line_resp = message_ann.end_lineno

existing_resp = {n.target.id for n in response_class.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
if "tool_calls" not in existing_resp:
    lines = text.splitlines()
    new_line = "    tool_calls: list[dict] | None = None"
    idx_resp = None
    for i, line in enumerate(lines):
        if i + 1 == insert_line_resp:
            idx_resp = i + 1
            break
    if idx_resp is None:
        print("ERROR: cannot find insertion point for tool_calls")
        sys.exit(1)
    lines.insert(idx_resp, new_line)
    text = "\n".join(lines)
else:
    print("Response field already present, skipping.")

# --- 3. Insert tool prompt block before model check ---
model_check = None
for n in ast.walk(ast.parse(text)):
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
    block_lines = block.splitlines() + ['']
    lines = lines[:insert_idx] + block_lines + lines[insert_idx:]
    text = "\n".join(lines)
else:
    print("Tool prompt block already present, skipping.")

# --- 4. Insert post-processing before 'return response' ---
return_stmt = None
for n in ast.walk(ast.parse(text)):
    if isinstance(n, ast.Return) and isinstance(n.value, ast.Name) and n.value.id == "response":
        return_stmt = n
        break
if return_stmt is None:
    print("ERROR: could not locate 'return response'")
    sys.exit(1)
insert_before_return = return_stmt.lineno

if "# If we injected a tool prompt" not in text:
    post_block = '''\
    # If we injected a tool prompt, parse the response for tool calls
    if req.tools:
        import json, re
        content = response.choices[0].message.content
        json_match = re.search(r'\\{.*\\}', content, re.DOTALL)
        if json_match:
            try:
                tool_data = json.loads(json_match.group(0))
                if 'tool_calls' in tool_data:
                    response.choices[0].message.tool_calls = tool_data['tool_calls']
                    response.choices[0].message.content = None
            except json.JSONDecodeError:
                pass

'''
    lines = text.splitlines()
    insert_idx_ret = insert_before_return - 1
    block_lines_ret = post_block.splitlines() + ['']
    lines = lines[:insert_idx_ret] + block_lines_ret + lines[insert_idx_ret:]
    text = "\n".join(lines)
else:
    print("Post-processing block already present, skipping.")

# Write final text
path.write_text(text)
print(f"Updated {path}")
PY

# Validate syntax
python -m py_compile "$CHAT_FILE"
log "✅ Tool calling support added successfully."
