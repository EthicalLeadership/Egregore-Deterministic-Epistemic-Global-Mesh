"""Agent runner — execute CLI agents on behalf of chat users."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from egregore.application.agent_registry import AgentSpec
from egregore.shared.canonical import canonical_dumps

# Environment keys that are forwarded to agent CLIs so they can call LLMs/APIs.
_FORWARDED_ENV_KEYS = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "KIMI_API_KEY",
    "KIMI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OLLAMA_BASE_URL",
    "OLLAMA_DEFAULT_MODEL",
]


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int
    duration_ms: float
    timed_out: bool = False
    error: str = ""


class AgentRunner:
    """Run a CLI agent with the user's instruction and session context."""

    def __init__(self, default_timeout: float = 120.0):
        self.default_timeout = default_timeout

    def run(
        self,
        spec: AgentSpec,
        instruction: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Execute the agent CLI and return captured output."""
        timeout = spec.timeout or self.default_timeout

        env = os.environ.copy()
        env["BLACKSTAR_AGENT_CONTEXT"] = canonical_dumps(context, default=str)
        for key in _FORWARDED_ENV_KEYS:
            if key in os.environ:
                env[key] = os.environ[key]

        start = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603
                [str(spec.path), instruction],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=str(spec.path.parent),
            )
            duration_ms = (time.monotonic() - start) * 1000
            return AgentResult(
                ok=proc.returncode == 0,
                stdout=proc.stdout,
                stderr=proc.stderr,
                returncode=proc.returncode,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = (time.monotonic() - start) * 1000
            return AgentResult(
                ok=False,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                returncode=-1,
                duration_ms=duration_ms,
                timed_out=True,
                error=f"Agent timed out after {timeout} seconds",
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            return AgentResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                duration_ms=duration_ms,
                error=str(exc) or f"{type(exc).__name__}",
            )
