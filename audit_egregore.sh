
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$ROOT_DIR/src"

echo "🔎 Egregore Capability Audit"
echo "============================"
echo "Project root: $ROOT_DIR"
echo ""

# 1. List likely AI-related files
echo "📁 Files that may be AI/inference related:"
find "$SRC_DIR" -type f \( -name "*.py" -o -name "*.ts" -o -name "*.tsx" -o -name "*.mjs" -o -name "*.js" -o -name "*.json" \) \
  | grep -Ei '(model|llm|gpt|inference|chat|completion|embedding|vision|audio|voice|whisper|transform|torch|llama|gguf|onnx|server|api|endpoint|websocket)' \
  | sort
echo ""

# 2. Check dependencies
echo "📦 Dependencies found:"
if [ -f "$ROOT_DIR/package.json" ]; then
  echo "--- Node.js dependencies (package.json) ---"
  grep -E '"(llama-cpp|transformers|torch|onnxruntime|whisper|express|fastify|socket.io|openai|@huggingface|@xenova)' "$ROOT_DIR/package.json" || echo "  (no matches)"
fi
if [ -f "$ROOT_DIR/requirements.txt" ]; then
  echo "--- Python dependencies (requirements.txt) ---"
  grep -E '(llama-cpp-python|transformers|torch|onnxruntime|whisper|fastapi|flask|uvicorn|openai|sentence-transformers)' "$ROOT_DIR/requirements.txt" || echo "  (no matches)"
fi
if [ -f "$ROOT_DIR/pyproject.toml" ]; then
  echo "--- Python dependencies (pyproject.toml) ---"
  grep -E '(llama-cpp-python|transformers|torch|onnxruntime|whisper|fastapi|flask|uvicorn|openai|sentence-transformers)' "$ROOT_DIR/pyproject.toml" || echo "  (no matches)"
fi
echo ""

# 3. Search for server or API code
echo "🌐 Possible API / server routes:"
grep -RniE '(app\.(get|post|put|delete)|router\.(get|post|put|delete)|fastapi|flask|express|http\.createServer|websocket|ws\.on|@app\.route|@router\.route)' "$SRC_DIR" --include="*.py" --include="*.ts" --include="*.tsx" --include="*.mjs" --include="*.js" | head -n 50 || echo "  (no matches)"
echo ""

# 4. Look for model loading / inference patterns
echo "🤖 Model loading / inference code:"
grep -RniE '(llama_cpp|Llama\(|from_pretrained|AutoModel|pipeline\(|load_model|torch\.load|onnxruntime|whisper\.load_model|generate\()' "$SRC_DIR" --include="*.py" --include="*.ts" --include="*.tsx" --include="*.mjs" --include="*.js" | head -n 50 || echo "  (no matches)"
echo ""

# 5. Check for model files on disk (quick scan of common extensions)
echo "💾 Model files found (top‑level scan):"
find "$ROOT_DIR" -maxdepth 3 -type f \( -name "*.gguf" -o -name "*.bin" -o -name "*.safetensors" -o -name "*.onnx" -o -name "*.pt" -o -name "*.pth" \) | head -n 20 || echo "  (none found in first 3 levels)"
echo ""

echo "✅ Audit complete. Review the output above."
