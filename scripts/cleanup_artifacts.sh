#!/usr/bin/env bash
set -euo pipefail

echo "=== BLACKSTAR ARTIFACT CLEANUP ==="

# 1. Remove all __pycache__ directories and compiled Python artifacts
#    Skip .venv because installed packages legitimately contain __pycache__.
find . -type d -name "__pycache__" -not -path "./.venv/*" -prune -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -not -path "./.venv/*" -delete 2>/dev/null || true
find . -type f -name "*.pyo" -not -path "./.venv/*" -delete 2>/dev/null || true

# 2. Remove editor / AI assistant cache directories
rm -rf .sixth/ .aider/ .aider* 2>/dev/null || true

# 3. Remove orphan .py files at the repo root that are not tracked by git
if command -v git >/dev/null 2>&1; then
    git ls-files -o --exclude-standard '*.py' 2>/dev/null | while IFS= read -r f; do
        if [ "$(dirname "$f")" = "." ]; then
            echo "Removing orphan root file: $f"
            rm -f "$f"
        fi
    done
fi

# 4. Remove old build artifacts
rm -rf build/ dist/ *.egg-info/ .eggs/ wheels/ 2>/dev/null || true

# 5. Remove pytest cache
rm -rf .pytest_cache/ 2>/dev/null || true

# 6. Verify no __pycache__ remains (outside .venv)
if find . -type d -name "__pycache__" -not -path "./.venv/*" | grep -q .; then
    echo "ERROR: __pycache__ still exists!"
    find . -type d -name "__pycache__" -not -path "./.venv/*"
    exit 1
fi

# 7. Verify no .sixth/ or .aider/ remains
if [ -d ".sixth" ] || [ -d ".aider" ]; then
    echo "ERROR: .sixth/ or .aider/ still exists!"
    exit 1
fi

echo "=== CLEANUP COMPLETE ==="
echo "Zero __pycache__ directories remaining."
echo "Zero .sixth/ or .aider/ artifacts remaining."
echo "Zero orphan .py files at root."
