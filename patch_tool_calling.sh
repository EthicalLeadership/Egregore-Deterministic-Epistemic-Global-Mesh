#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$HOME/egregore"
REQUEST_FILE="src/egregore/domain/inference_models.py"
CHAT_FILE="src/egregore/http_api/http/v1/chat.py"
BACKUP_DIR="backups/tool_calling_patch"

log()  { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }

usage() {
    cat <<USAGE
Usage: $0 [OPTIONS]

Options:
  --dry-run       Show changes without writing files.
  --no-backup     Skip creating backups.
  -h, --help      Show this help message.
USAGE
}

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

cd "$PROJECT_DIR"

for f in "$REQUEST_FILE" "$CHAT_FILE"; do
    [[ -f "$f" ]] || { err "File not found: $f"; exit 1; }
done

BACKUP_TIMESTAMP=""
if [[ "$NO_BACKUP" == false && "$DRY_RUN" == false ]]; then
    mkdir -p "$BACKUP_DIR"
    BACKUP_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    cp "$REQUEST_FILE" "$BACKUP_DIR/inference_models.py.$BACKUP_TIMESTAMP.bak"
    cp "$CHAT_FILE" "$BACKUP_DIR/chat.py.$BACKUP_TIMESTAMP.bak"
    log "Backups created in $BACKUP_DIR"
fi

if [[ "$NO_BACKUP" == false && "$DRY_RUN" == false ]]; then
    trap 'err "An error occurred. Restoring backups..."; \
          cp "$BACKUP_DIR/inference_models.py.$BACKUP_TIMESTAMP.bak" "$REQUEST_FILE"; \
          cp "$BACKUP_DIR/chat.py.$BACKUP_TIMESTAMP.bak" "$CHAT_FILE";' ERR
fi

python - "$DRY_RUN" "$REQUEST_FILE" "$CHAT_FILE" <<'PY'
import sys, ast
from pathlib import Path

dry_run = sys.argv[1].lower() == "true"
request_file = Path(sys.argv[2])
chat_file = Path(sys.argv[3])

def modify_inference_models(path):
    text = path.read_text()
    tree = ast.parse(text)
    request_class = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChatCompletionRequest"), None)
    if request_class is None:
        print("ERROR: ChatCompletionRequest not found in", path)
        sys.exit(1)
    messages_ann = next((n for n in request_class.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "messages"), None)
    if messages_ann is None:
        print("ERROR: 'messages' annotation not found")
        sys.exit(1)
    insert_line = messages_ann.end_lineno
    existing = {n.target.id for n in request_class.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    if "tools" in existing and "tool_choice" in existing:
        print("inference_models.py: tools/tool_choice already present.")
    else:
        lines = text.splitlines()
        additions = []
        if "tools" not in existing:
            additions.append("    tools: list[dict] | None = None")
        if "tool_choice" not in existing:
            additions.append("    tool_choice: str | dict | None = None")
        # Insert after the messages line (1-based)
        idx = None
        for i, line in enumerate(lines):
            if i + 1 == insert_line:
                idx = i + 1
                break
        if idx is None:
            print("ERROR: could not locate insertion point")
            sys.exit(1)
        for j, add in enumerate(additions):
            lines.insert(idx + j, add)
        text = "\n".join(lines)

    tree = ast.parse(text)
    response_class = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChatCompletionResponse"), None)
    if response_class is None:
        print("ERROR: ChatCompletionResponse not found")
        sys.exit(1)
    message_ann = next((n for n in response_class.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "message"), None)
    if message_ann is None:
        print("ERROR: 'message' annotation not found in response")
        sys.exit(1)
    insert_line_resp = message_ann.end_lineno
    existing_resp = {n.target.id for n in response_class.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    if "tool_calls" in existing_resp:
        print("inference_models.py: tool_calls already present.")
    else:
        lines = text.splitlines()
        new_line = "    tool_calls: list[dict] | None = None"
        idx_resp = None
        for i, line in enumerate(lines):
            if i + 1 == insert_line_resp:
                idx_resp = i + 1
                break
        if idx_resp is None:
            print("ERROR: could not locate response insertion point")
            sys.exit(1)
        lines.insert(idx_resp, new_line)
        text = "\n".join(lines)

    if dry_run:
        print(f"[DRY-RUN] Would update {path}")
    else:
        path.write_text(text)
        print(f"Updated {path}")

def modify_chat(path):
    text = path.read_text()
    tree = ast.parse(text)
    model_check = next((n for n in ast.walk(tree) if isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp) and isinstance(n.test.op, ast.Not) and isinstance(n.test.operand, ast.Call) and isinstance(n.test.operand.func, ast.Attribute) and n.test.operand.func.attr == "model_exists"), None)
    if model_check is None:
        print("ERROR: could not locate model_exists check")
        sys.exit(1)
    insert_before = model_check.lineno

    if "# If tools are provided, use prompt-based tool calling" in text:
        print("chat.py: tool handling already present.")
    else:
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
        # Create a new request with updated messages and no tools
        from egregore.domain.inference_models import ChatCompletionRequest
        req = ChatCompletionRequest(
            model=req.model,
            messages=messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            stream=req.stream,
        )

'''
        lines = text.splitlines()
        insert_idx = insert_before - 1
        block_lines = block.splitlines() + ['']
        lines = lines[:insert_idx] + block_lines + lines[insert_idx:]
        text = "\n".join(lines)

    tree = ast.parse(text)
    return_stmt = next((n for n in ast.walk(tree) if isinstance(n, ast.Return) and isinstance(n.value, ast.Name) and n.value.id == "response"), None)
    if return_stmt is None:
        print("ERROR: could not locate return response")
        sys.exit(1)
    insert_before_return = return_stmt.lineno

    if "# If we injected a tool prompt, parse the response for tool calls" in text:
        print("chat.py: post-processing already present.")
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

if [[ "$DRY_RUN" == false ]]; then
    log "Validating syntax..."
    python -m py_compile "$REQUEST_FILE" "$CHAT_FILE"
    log "Syntax validation passed."
else
    warn "Dry-run requested, skipping validation."
fi

log "✅ Tool calling support patch applied successfully."
