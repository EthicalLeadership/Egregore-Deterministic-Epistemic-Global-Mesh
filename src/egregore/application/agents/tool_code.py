"""Safe code execution tool."""

from __future__ import annotations

import subprocess
import tempfile
import os

def run_python(code: str) -> str:
    """Run Python code in a subprocess with a short timeout."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        proc = subprocess.run(
            ["/mnt/blackstar/vol-hdd-a/home_data_blackstar/egregore/.venv/bin/python", path],
            capture_output=True, text=True, timeout=10
        )
        output = proc.stdout + proc.stderr
        return output[:2000] or "(no output)"
    except subprocess.TimeoutExpired:
        return "Execution timed out."
    finally:
        os.unlink(path)

def run_javascript(code: str) -> str:
    """Run JavaScript code using node if available."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        proc = subprocess.run(
            ["node", path],
            capture_output=True, text=True, timeout=10
        )
        output = proc.stdout + proc.stderr
        return output[:2000] or "(no output)"
    except FileNotFoundError:
        return "Node.js is not installed."
    except subprocess.TimeoutExpired:
        return "Execution timed out."
    finally:
        os.unlink(path)
