"""Additional tools for the Anchorum agent: file and web search."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List
import urllib.request
import urllib.parse

WORKSPACE_ROOT = Path(os.environ.get("EGREGORE_AGENT_CWD", Path.cwd())).resolve()

def list_files() -> List[str]:
    """List files in workspace (max 200)."""
    files = []
    for p in WORKSPACE_ROOT.rglob("*"):
        if p.is_file() and not any(part.startswith('.') for part in p.parts):
            files.append(str(p.relative_to(WORKSPACE_ROOT)))
            if len(files) >= 200:
                break
    return sorted(files)

def search_files(query: str) -> List[str]:
    """Search filenames and content using system grep (fast, timeout)."""
    import subprocess
    try:
        result = subprocess.run(
            ["grep", "-ril", "--max-count=1", query, str(WORKSPACE_ROOT)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        paths = []
        for line in result.stdout.splitlines():
            rel = Path(line).relative_to(WORKSPACE_ROOT).as_posix()
            paths.append(rel)
            if len(paths) >= 50:
                break
        return paths
    except subprocess.TimeoutExpired:
        return []
    except Exception:
        return []

def read_file(relative_path: str) -> str:
    """Read a file from workspace, capped to 5000 chars."""
    full = (WORKSPACE_ROOT / relative_path).resolve()
    if not str(full).startswith(str(WORKSPACE_ROOT)):
        return "Path outside workspace."
    try:
        return full.read_text(errors="ignore")[:5000]
    except Exception as e:
        return f"Read error: {e}"

def web_search(query: str) -> str:
    """Perform a web search using DuckDuckGo Lite with short timeout."""
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        results = re.findall(
            r'<a rel="nofollow" class="result__a" href="([^"]+)">([^<]+)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )
        if not results:
            return "No results found."
        formatted = []
        for href, title, snippet in results[:5]:
            clean_snippet = re.sub(r"<.*?>", "", snippet).strip()
            formatted.append(f"- {title}\n  {clean_snippet}\n  {href}")
        return "\n".join(formatted)
    except Exception as e:
        return f"Web search failed (network likely unavailable): {e}"

def search_canlii(query: str) -> str:
    """Search CanLII for Canadian legal cases/legislation."""
    return web_search(f"{query} site:canlii.org")
