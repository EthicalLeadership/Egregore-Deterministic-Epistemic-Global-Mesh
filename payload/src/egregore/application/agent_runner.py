"""Agent runner — execute CLI agents on behalf of chat users."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from egregore.application.agent_registry import AgentSpec
from egregore.application.identity_port import verify_identity
from egregore.domain.task_intent import identity_satisfies_privilege, parse_task_intent
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
    warning: str = ""


class AgentRunner:
    """Run a CLI agent with the user's instruction and session context."""

    def __init__(self, default_timeout: float = 120.0):
        self.default_timeout = default_timeout

    @staticmethod
    def _primary_role_from_identity(identity_payload: dict[str, Any]) -> str:
        roles_value = identity_payload.get("roles")
        if not isinstance(roles_value, list):
            return "read"
        roles = {str(role).strip().lower() for role in roles_value}
        for candidate in ("admin", "operator", "read", "reader"):
            if candidate in roles:
                return "read" if candidate == "reader" else candidate
        return "read"

    def run(
        self,
        spec: AgentSpec,
        instruction: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Execute the agent CLI and return captured output."""
        timeout = spec.timeout or self.default_timeout

        token = context.get("caller_identity_token")
        if not isinstance(token, str) or not token.strip():
            return AgentResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                duration_ms=0.0,
                error="Verified caller_identity_token is required for agent execution.",
            )

        verifier = context.get("identity_verifier")
        try:
            if verifier is not None:
                if not callable(verifier):
                    raise ValueError("identity_verifier must be callable")
                caller_identity = verifier(token.strip())
            else:
                caller_identity = verify_identity(token.strip())
        except Exception as exc:
            return AgentResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                duration_ms=0.0,
                error=f"Identity verification failed: {exc}",
            )

        if not isinstance(caller_identity, dict):
            return AgentResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                duration_ms=0.0,
                error="Identity verifier returned invalid caller identity payload.",
            )

        actor_role = self._primary_role_from_identity(caller_identity)
        if actor_role not in spec.allowed_roles:
            return AgentResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                duration_ms=0.0,
                error=(
                    f"Agent '{spec.name}' requires one of these roles: "
                    f"{', '.join(sorted(spec.allowed_roles))}. "
                    f"Your role is '{actor_role or 'unknown'}'."
                ),
            )

        allow_legacy = bool(context.get("allow_legacy_task_intent", False))
        try:
            task_intent, warning = parse_task_intent(
                instruction,
                allow_legacy=allow_legacy,
                legacy_role=actor_role or "read",
            )
        except ValueError as exc:
            return AgentResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                duration_ms=0.0,
                error=str(exc),
            )

        if not identity_satisfies_privilege(
            caller_identity, task_intent.required_privilege
        ):
            return AgentResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                duration_ms=0.0,
                error=(
                    f"TaskIntent requires {task_intent.required_privilege} privilege. "
                    "Resolved caller identity does not satisfy that requirement."
                ),
            )

        env = os.environ.copy()
        enriched_context = dict(context)
        enriched_context.pop("caller_identity_token", None)
        enriched_context.pop("identity_verifier", None)
        enriched_context["task_intent"] = task_intent.to_payload()
        enriched_context["caller_identity"] = caller_identity
        enriched_context["role"] = actor_role
        env["EGREGORE_AGENT_CONTEXT"] = canonical_dumps(enriched_context, default=str)
        for key in _FORWARDED_ENV_KEYS:
            if key in os.environ:
                env[key] = os.environ[key]

        start = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603
                [str(spec.path), task_intent.to_json()],
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
                warning=warning,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = (time.monotonic() - start) * 1000
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return AgentResult(
                ok=False,
                stdout=stdout,
                stderr=stderr,
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
