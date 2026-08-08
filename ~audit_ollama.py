#!/usr/bin/env python3
"""
Audit your codebase for any usage of Ollama.
Scans files for:
- "ollama" string (case-insensitive)
- localhost:11434 (Ollama's default port)
- ollama CLI commands (pull, serve, run, etc.)
- imports like "import ollama" or "from ollama import ..."
- any subprocess calls with "ollama" in them
Outputs a JSON report with file paths, line numbers, and the matching line.
"""
import os
import re
import argparse
import json
from pathlib import Path

# Patterns to search for – all case-insensitive
PATTERNS = [
    r'ollama',                       # generic
    r'localhost:11434',              # default API port
    r'127\.0\.0\.1:11434',
    r'ollama\.run',                 # subprocess calls
    r'ollama\s+pull',
    r'ollama\s+serve',
    r'ollama\s+list',
    r'from ollama import',
    r'import ollama',
    r'require\s*\(\s*[\'"]ollama',  # Node.js require
    r'ollama\.js',                  # node module
    r'ollama\s+run',                # CLI
]

# File extensions to scan (code and config)
SCAN_EXTS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.sh', '.bash',
    '.json', '.yaml', '.yml', '.toml', '.ini', '.conf',
    '.cfg', '.env', '.txt', '.md', '.html', '.css', '.jsx', '.tsx'
}

def scan_file(filepath):
    """Return list of (line_number, matched_text) for Ollama references."""
    hits = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for i, line in enumerate(f, 1):
                line_lower = line.lower()
                for pat in PATTERNS:
                    if re.search(pat, line, re.IGNORECASE):
                        hits.append((i, line.strip()))
                        break  # avoid duplicate lines
    except (OSError, UnicodeDecodeError):
        pass
    return hits

def main():
    parser = argparse.ArgumentParser(description="Audit code for Ollama usage.")
    parser.add_argument('--root', nargs='+', default=['/home/kark/egregore'],
                        help='Root directories to scan (default: /home/kark/egregore)')
    parser.add_argument('--output', default='ollama_usage.json',
                        help='Output JSON file')
    parser.add_argument('--exclude', nargs='*', default=[],
                        help='Directories to skip (e.g., .venv)')
    args = parser.parse_args()

    skip = set(args.exclude)
    report = {}

    for root in args.root:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Skip excluded directories
            if any(dirpath.startswith(s) for s in skip):
                continue
            # Also skip .git, __pycache__, node_modules, etc.
            if '.git' in dirnames:
                dirnames.remove('.git')
            if '__pycache__' in dirnames:
                dirnames.remove('__pycache__')
            if 'node_modules' in dirnames:
                dirnames.remove('node_modules')

            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in SCAN_EXTS:
                    continue
                full = os.path.join(dirpath, fname)
                hits = scan_file(full)
                if hits:
                    report[full] = hits

    with open(args.output, 'w') as f:
        json.dump(report, f, indent=2)

    print(f"Audit complete. Found {len(report)} files with Ollama references.")
    print(f"Report saved to {args.output}")

if __name__ == '__main__':
    main()
