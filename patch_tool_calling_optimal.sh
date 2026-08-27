#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Optimal Tool Calling Patch for Egregore
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

# Create backups (unless disabled)
BACKUP_TIMESTAMP=""
if [[ "$NO_BACKUP" == false && "$DRY_RUN" == false ]]; then
    mkdir -p "$BACKUP_DIR"
    BACKUP_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    cp "$REQUEST_FILE" "$BACKUP_DIR/inference_models.py.$BACKUP_TIMESTAMP.bak"
    cp "$CHAT_FILE" "$BACKUP_DIR/chat.py.$BACKUP_TIMESTAMP.bak"
    log "Backups created in $BACKUP_DIR"
fi

# Trap to restore backups on error
if [[ "$NO_BACKUP" == false && "$DRY_RUN" == false ]]; then
    trap 'err "An error occurred. Restoring backups..."; \
          cp "$BACKUP_DIR/inference_models.py.$BACKUP_TIMESTAMP.bak" "$REQUEST_FILE"; \
          cp "$BACKUP_DIR/chat.py.$BACKUP_TIMESTAMP.bak" "$CHAT_FILE";' ERR
fi

# Python patcher
python - "$DRY_RUN" "$REQUEST_FILE" "$CHAT_FILE" <<'PY'
import sys
import ast
import json
from pathlib import Path

dry_run = sys.argv[1].lower() == "true"
request_file = Path(sys.argv[2])
chat_file = Path(sys.argv[3])

# --------------------------------------------------------------------------
# 1. Modify inference_models.py (add fields to request/response classes)
# --------------------------------------------------------------------------
def modify_inference_models(path: Path):
    text = path.read_text()
    tree = ast.parse(text)

    # --- Locate ChatCompletionRequest ---
    request_class = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChatCompletionRequest"),
        None
    )
    if request_class is None:
        print("ERROR: ChatCompletionRequest not found in", path)
        sys.exit(1)

    messages_ann = next(
        (n for n in request_class.body if isinstance(n, ast.AnnAssign)
         and isinstance(n.target, ast.Name) and n.target.id == "messages"),
        None
    )
    if messages_ann is None:
        print("ERROR: 'messages' annotation not found in ChatCompletionRequest")
        sys.exit(1)

    insert_line_req = messages_ann.end_lineno  # 1‑based line number of the messages annotation

    existing_req = {
        n.target.id for n in request_class.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }
    if "tools" in existing_req and "tool_choice" in existing_req:
        print("inference_models.py: request fields already present, skipping.")
    else:
        lines = text.splitlines()
        additions = []
        if "tools" not in existing_req:
            additions.append("    tools: list[dict] | None = None")
        if "tool_choice" not in existing_req:
            additions.append("    tool_choice: str | dict | None = None")
        # Insert after the line containing 'messages' (1‑based index)
        idx_req = None
        for i, line in enumerate(lines):
            if i + 1 == insert_line_req:
                idx_req = i + 1
                break
        if idx_req is None:
            print("ERROR: could not locate insertion point for request fields")
            sys.exit(1)
        for j, add in enumerate(additions):
            lines.insert(idx_req + j, add)
        text = "\n".join(lines)

    # --- Locate ChatCompletionResponse ---
    # Re‑parse after possible modification
    tree = ast.parse(text)
    response_class = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChatCompletionResponse"),
        None
    )
    if response_class is None:
        print("ERROR: ChatCompletionResponse not found in", path)
        sys.exit(1)

    message_ann = next(
        (n for n in response_class.body if isinstance(n, ast.AnnAssign)
         and isinstance(n.target, ast.Name) and n.target.id == "message"),
        None
    )
    if message_ann is None:
        print("ERROR: 'message' annotation not found in ChatCompletionResponse")
        sys.exit(1)

    insert_line_resp = message_ann.end_lineno

    existing_resp = {
        n.target.id for n in response_class.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }
    if "tool_calls" in existing_resp:
        print("inference_models.py: response field already present, skipping.")
    else:
        lines = text.splitlines()
        new_line = "    tool_calls: list[dict] | None = None"
        idx_resp = None
        for i, line in enumerate(lines):
            if i + 1 == insert_line_resp:
                idx_resp = i + 1
                break
        if idx_resp is None:
            print("ERROR: could not locate insertion point for tool_calls")
            sys.exit(1)
        lines.insert(idx_resp, new_line)
        text = "\n".join(lines)

    if dry_run:
        print(f"[DRY-RUN] Would update {path}")
    else:
        path.write_text(text)
        print(f"Updated {path}")

# --------------------------------------------------------------------------
# 2. Modify chat.py (add tool prompt injection and post‑processing)
# --------------------------------------------------------------------------
def modify_chat(path: Path):
    text = path.read_text()
    tree = ast.parse(text)

    # Locate the If node with test "not service.model_exists(req.model)"
    model_check = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.If)
         and isinstance(n.test, ast.UnaryOp)
         and isinstance(n.test.op, ast.Not)
         and isinstance(n.test.operand, ast.Call)
         and isinstance(n.test.operand.func, ast.Attribute)
         and n.test.operand.func.attr == "model_exists"),
        None
    )
    if model_check is None:
        print("ERROR: could not locate model_exists check in", path)
        sys.exit(1)

    insert_before = model_check.lineno  # 1‑based line number

    # Insert tool‑prompt block before the model check (if not already present)
    if "# If tools are provided" not in text:
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

        # Reconstruct request with all original fields except tools/tool_choice
        # This preserves any additional parameters (top_p, stop, etc.)
        if hasattr(req, "model_dump"):
            req_dict = req.model_dump(exclude={"tools", "tool_choice"})
        else:
            req_dict = req.dict(exclude={"tools", "tool_choice"})
        req_dict["messages"] = messages
        req = ChatCompletionRequest(**req_dict)

'''
        lines = text.splitlines()
        insert_idx = insert_before - 1
        block_lines = tool_block.splitlines() + ['']
        lines = lines[:insert_idx] + block_lines + lines[insert_idx:]
        text = "\n".join(lines)
    else:
        print("chat.py: tool prompt block already present, skipping.")

    # Insert post‑processing block before the "return response" statement
    tree = ast.parse(text)
    return_stmt = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.Return)
         and isinstance(n.value, ast.Name)
         and n.value.id == "response"),
        None
    )
    if return_stmt is None:
        print("ERROR: could not locate 'return response' in", path)
        sys.exit(1)

    insert_before_return = return_stmt.lineno

    if "# If we injected a tool prompt" not in text:
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
    else:
        print("chat.py: post‑processing block already present, skipping.")

    if dry_run:
        print(f"[DRY-RUN] Would update {path}")
    else:
        path.write_text(text)
        print(f"Updated {path}")

# --------------------------------------------------------------------------
# Run both modifications
# --------------------------------------------------------------------------
modify_inference_models(request_file)
modify_chat(chat_file)
PY

# Validate modified files
if [[ "$DRY_RUN" == false ]]; then
    log "Validating syntax..."
    python -m py_compile "$REQUEST_FILE" "$CHAT_FILE"
    log "Syntax validation passed."
else
    warn "Dry‑run requested, skipping validation."
fi

log "✅ Tool calling support patch applied successfully."
