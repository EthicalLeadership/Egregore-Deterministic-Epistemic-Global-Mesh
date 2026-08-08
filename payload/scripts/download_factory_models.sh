#!/usr/bin/env bash
# Download the first set of quantized models for the Egregore 5-station factory.
# Targets Pioneer 1's RTX 3060 12GB: one tiny/multipurpose model and one 7B model.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MODEL_DIR="${MODELS_DIR}/gguf"

# URL, target subdir, target filename
MODELS=(
  "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf|general|qwen2.5-1.5b-instruct-q4_k_m.gguf"
  "https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q4_K_M.gguf|expert|Qwen2.5-7B-Instruct-Q4_K_M.gguf"
  "https://huggingface.co/TheBloke/deepseek-coder-6.7B-instruct-GGUF/resolve/main/deepseek-coder-6.7b-instruct.Q4_K_M.gguf|specialized|deepseek-coder-6.7b-instruct.Q4_K_M.gguf"
)

mkdir -p "$MODEL_DIR"/{general,expert,specialized}

download_model() {
  local spec="$1"
  local url="${spec%%|*}"
  local subdir="${spec#*|}"
  subdir="${subdir%|*}"
  local filename="${spec##*|}"
  local target="$MODEL_DIR/$subdir/$filename"

  if [[ -f "$target" ]]; then
    echo "✓ Already present: $target"
    return 0
  fi

  echo "⬇ Downloading $filename ..."
  if command -v wget >/dev/null 2>&1; then
    wget --show-progress -q -O "$target" "$url"
  elif command -v curl >/dev/null 2>&1; then
    curl -L --progress-bar -o "$target" "$url"
  else
    echo "ERROR: wget or curl required" >&2
    exit 1
  fi

  echo "✓ Saved: $target"
}

for spec in "${MODELS[@]}"; do
  download_model "$spec"
done

echo ""
echo "Factory model pool ready at: $MODEL_DIR"
find "$MODEL_DIR" -type f -name "*.gguf" -printf '  %p\t%s bytes\n'
