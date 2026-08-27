#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Patch Egregore chat endpoint with prompt‑based tool calling support
# ---------------------------------------------------------------------------

PROJECT_DIR="$HOME/egregore"
REQUEST_FILE="src/egregore/domain/inference_models.py"
CHAT_FILE="src/egregore/http_api/http/v1/chat.py"
BACKUP_DIR="backups/tool_calling_patch"

# ----------------------------- Logging ------------------------------------
log()  { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --dry-run       Show changes without writing files.
  --no-backup     Skip creating backups.
  -h, --help      Show this help message.
EOF
}

# ----------------------------- Argument Parsing ---------------------------
DRY_RUN=false
NO_BACKUP=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)    DRY_RUN=true ;;
        --no-backup)  NO_BACKUP=true ;;
        -h|--help)    usage; exit 0 ;;
        *) err "Unknown option: $1"; usage; exit 1 ;;
    esac
    shift
done

# ----------------------------- Main Script --------------------------------
cd "$PROJECT_DIR"

# Verify files exist
for f in "$REQUEST_FILE" "$CHAT_FILE"; do
    if [[ ! -f "$f" ]]; then
        err "File not found: $f"
        exit 1
    fi
done

# Create backups unless disabled
BACKUP_TIMESTAMP=""
if [[ "$NO_BACKUP" == false && "$DRY_RUN" == false ]]; then
    mkdir -p "$BACKUP_DIR"
    BACKUP_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    cp "$REQUEST_FILE" "$BACKUP_DIR/inference_models.py.$BACKUP_TIMESTAMP.bak"
    cp "$CHAT_FILE" "$BACKUP_DIR/chat.py.$BACKUP_TIMESTAMP.bak"
    log "Backups created in $BACKUP_DIR (timestamp: $BACKUP_TIMESTAMP)"
fi

# Trap to restore backups on error
if [[ "$NO_BACKUP" == false && "$DRY_RUN" == false ]]; then
    trap 'err "An error occurred. Restoring backups..."; \
          cp "$BACKUP_DIR/inference_models.py.$BACKUP_TIMESTAMP.bak" "$REQUEST_FILE"; \
          cp "$BACKUP_DIR/chat.py.$BACKUP_TIMESTAMP.bak" "$CHAT_FILE";' ERR
fi

# Run Python patcher
python - "$DRY_RUN" "$REQUEST_FILE" "$CHAT_FILE" <<'PY'
import sys
import ast
from pathlib import Path

dry_run = sys.argv[1].lower() == "true"
request_file = Path(sys.argv[2])
chat_file = Path(sys.argv[3])

# --------------------------------------------------------------------------
# 1. Modify inference_models.py
# --------------------------------------------------------------------------
def modify_inference_models(path: Path):
    text = path.read_text()
    tree = ast.parse(text)

    # Locate ChatCompletionRequest and its 'messages' annotation
    request_class = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ChatCompletionRequest":
            request_class = node
            break
    if request_class is None:
        print("ERROR: ChatCompletionRequest class not found in inference_models.py")
        sys.exit(1)

    messages_ann = None
    for node in request_class.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "messages":
            messages_ann = node
            break
    if messages_ann is None:
        print("ERROR: 'messages' annotation not found in ChatCompletionRequest")
        sys.exit(1)

    insert_line_req = messages_ann.end_lineno  # Insert after this line

    # Check existing fields
    existing_names = set()
    for node in request_class.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            existing_names.add(node.target.id)

    if "tools" in existing_names and "tool_choice" in existing_names:
        print("inference_models.py: tools and tool_choice already present, skipping.")
    else:
        lines = text.splitlines()
        additions = []
        if "tools" not in existing_names:
            additions.append("    tools: list[dict] | None = None")
        if "tool_choice" not in existing_names:
            additions.append("    tool_choice: str | dict | None = None")
        # Insert additions after the line containing 'messages'
        # Find the actual line index (0-based) where insertion should happen
        insert_index = None
        for i, line in enumerate(lines):
            if i + 1 == insert_line_req:  # line numbers are 1-based
                insert_index = i + 1  # insert after this line
                break
        if insert_index is None:
            print("ERROR: Could not locate insertion point for tools/tool_choice")
            sys.exit(1)
        # Insert additions
        for j, add_line in enumerate(additions):
            lines.insert(insert_index + j, add_line)
        text = "\n".join(lines)

    # Locate ChatCompletionResponse and its 'message' annotation
    tree = ast.parse(text)  # re-parse after modification
    response_class = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ChatCompletionResponse":
            response_class = node
            break
    if response_class is None:
        print("ERROR: ChatCompletionResponse class not found in inference_models.py")
        sys.exit(1)

    message_ann = None
    for node in response_class.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "message":
            message_ann = node
            break
    if message_ann is None:
        print("ERROR: 'message' annotation not found in ChatCompletionResponse")
        sys.exit(1)

    insert_line_resp = message_ann.end_lineno

    existing_names_resp = set()
    for node in response_class.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            existing_names_resp.add(node.target.id)

    if "tool_calls" in existing_names_resp:
        print("inference_models.py: tool_calls already present, skipping.")
    else:
        lines = text.splitlines()
        new_line = "    tool_calls: list[dict] | None = None"
        insert_index_resp = None
        for i, line in enumerate(lines):
            if i + 1 == insert_line_resp:
                insert_index_resp = i + 1
                break
        if insert_index_resp is None:
            print("ERROR: Could not locate insertion point for tool_calls")
            sys.exit(1)
        lines.insert(insert_index_resp, new_line)
        text = "\n".join(lines)

    if dry_run:
        print(f"[DRY-RUN] Would update {path}")
    else:
        path.write_text(text)
        print(f"Updated {path}")

# --------------------------------------------------------------------------
# 2. Modify chat.py
# --------------------------------------------------------------------------
def modify_chat(path: Path):
    text = path.read_text()
    tree = ast.parse(text)

    # Find the If node with test "not service.model_exists(req.model)"
    model_check = None
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.UnaryOp) \
                and isinstance(node.test.op, ast.Not) \
                and isinstance(node.test.operand, ast.Call) \
                and isinstance(node.test.operand.func, ast.Attribute) \
                and node.test.operand.func.attr == "model_exists":
            model_check = node
            break
    if model_check is None:
        print("ERROR: Could not locate 'if not service.model_exists(req.model)' in chat.py")
        sys.exit(1)

    insert_before = model_check.lineno  # 1-based line number

    # Check if tool handling already present
    if "# If tools are provided, use prompt-based tool calling" in text:
        print("chat.py: tool handling already present, skipping first insertion.")
    else:
        tool_block = '''\
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
        # Create a new request with updated messages and no tools
        from egregore.domain.inference_models import ChatCompletionRequest
        req = ChatCompletionRequest(
            model=req.model,
            messages=messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            stream=req.stream,
            # include other fields as needed
        )

'''
        lines = text.splitlines()
        # Insert block before the line number (0-based index = line-1)
        insert_idx = insert_before - 1
        # The block ends with an empty line, splitlines removes trailing empty lines,
        # but we want to preserve it. Add a blank line after the block.
        block_lines = tool_block.splitlines() + ['']  # ensure trailing newline
        # Insert at the correct position
        lines = lines[:insert_idx] + block_lines + lines[insert_idx:]
        text = "\n".join(lines)

    # Find the "return response" statement (first occurrence)
    tree = ast.parse(text)
    return_stmt = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Name) \
                and node.value.id == "response":
            return_stmt = node
            break
    if return_stmt is None:
        print("ERROR: Could not locate 'return response' in chat.py")
        sys.exit(1)

    insert_before_return = return_stmt.lineno

    # Check if post-processing already present
    if "# If we injected a tool prompt, parse the response for tool calls" in text:
        print("chat.py: post-processing already present, skipping second insertion.")
    else:
        post_block = '''\
    # If we injected a tool prompt, parse the response for tool calls
    if req.tools:
        import json, re
        content = response.choices[0].message.content
        # Try to find JSON block
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

    if dry_run:
        print(f"[DRY-RUN] Would update {path}")
    else:
        path.write_text(text)
        print(f"Updated {path}")

modify_inference_models(request_file)
modify_chat(chat_file)
PY

# Validate modified files
if [[ "$DRY_RUN" == false ]]; then
    log "Validating syntax..."
    python -m py_compile "$REQUEST_FILE" "$CHAT_FILE"
    log "Syntax validation passed."
else
    warn "Dry-run requested, skipping validation."
fi

log "✅ Tool calling support patch applied successfully."
