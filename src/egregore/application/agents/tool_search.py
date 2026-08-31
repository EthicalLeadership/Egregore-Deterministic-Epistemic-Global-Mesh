"""Additional tools for the Anchorum agent: file and web search."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Dict, Any
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
    """Search filenames and content for a query."""
    q = query.lower()
    matches = []
    for f in WORKSPACE_ROOT.rglob("*"):
        if f.is_file() and not any(part.startswith('.') for part in f.parts):
            if q in f.name.lower():
                matches.append(str(f.relative_to(WORKSPACE_ROOT)))
                continue
            try:
                content = f.read_text(errors="ignore")[:10000].lower()
                if q in content:
                    matches.append(str(f.relative_to(WORKSPACE_ROOT)))
            except Exception:
                pass
            if len(matches) >= 50:
                break
    return matches

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
    """Perform a web search using DuckDuckGo Lite."""
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        # Extract result links and snippets
        results = re.findall(r'<a rel="nofollow" class="result__a" href="([^"]+)">([^<]+)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        if not results:
            return "No results found."
        formatted = []
        for href, title, snippet in results[:5]:
            clean_snippet = re.sub(r"<.*?>", "", snippet).strip()
            formatted.append(f"- {title}
  {clean_snippet}
  {href}")
        return "
".join(formatted)
    except Exception as e:
        return f"Web search failed: {e}"

def search_canlii(query: str) -> str:
    """Search CanLII for Canadian legal cases/legislation."""
    url = f"https://www.canlii.org/en/#search/text={urllib.parse.quote_plus(query)}"
    return web_search(f"{query} site:canlii.org")

if __name__ == "__main__":
    print(list_files())
