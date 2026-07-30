"""Chat command interpreter — routes WebSocket messages to Egregore/ANCHORUM operations."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from egregore.application.agent_runner import AgentRunner
from egregore.application.dossier_generate_service import DossierGenerateRequest
from egregore.application.http_v1_facades import generate_v1_dossier
from egregore.domain.inference_models import ChatMessage, ChatRequest, InferenceMode
from egregore.domain.semantics_models import CommandAck
from egregore.governance.permissions import Action, PermissionService
from egregore.interface.rag_api import RAGQuery, query_rag
from egregore.models.user import UserIdentity
from egregore.shared.canonical import canonical_dumps, canonical_loads, sha256_hex

_CHAT_HISTORY_KEY = "chat_history"
_CHAT_HISTORY_MAX_TURNS = 20  # user + assistant pairs


@dataclass(frozen=True)
class ChatContext:
    """Runtime context for a chat session."""

    session_id: str
    user_id: str
    role: str  # legacy primary role; prefer identity.roles
    env: dict[str, str] = field(default_factory=dict)
    identity: UserIdentity | None = None


# Operations that require elevated privileges.
PRIVILEGED_ROLES = {"admin", "operator"}


def _permission_service() -> PermissionService:
    return PermissionService()


def _identity_from_context(context: ChatContext) -> UserIdentity:
    if context.identity is not None:
        return context.identity
    return UserIdentity(
        tenant_id="default",
        user_id=context.user_id,
        username=context.user_id,
        email=None,
        roles=[context.role],
        vertical_grants=[],
        status="active",
    )


def _deny(reason: str, command: str) -> dict[str, Any]:
    return {
        "type": "chat",
        "command": command,
        "ok": False,
        "summary": reason,
        "detail": None,
    }


def _get_inference_service(context: ChatContext) -> Any:
    """Return the inference service from context, or None if not wired."""
    svc = context.env.get("inference_service")
    return svc


def _get_agent_registry(context: ChatContext) -> Any:
    """Return the agent registry from context, or None if not wired."""
    return context.env.get("agent_registry")


def _default_chat_model() -> str:
    """Resolve default chat model from environment."""
    return os.environ.get("EGREGORE_CHAT_MODEL", "my-coder-ft")


def _best_local_gguf_model() -> str:
    """Return the largest present GGUF model from the catalog."""
    try:
        from egregore.infrastructure.gguf_catalog import GGUF_ROOT, GGUFCatalog
    except Exception:
        return _default_chat_model()

    catalog = GGUFCatalog()
    best_id: str | None = None
    best_size = 0.0
    for model_id, entry in catalog._entries.items():
        path = GGUF_ROOT / entry.tier / entry.filename
        if not path.exists():
            continue
        try:
            size = float(entry.parameters.lower().replace("b", "").strip())
        except Exception:
            size = 0.0
        if size > best_size:
            best_size = size
            best_id = model_id
    return best_id or _default_chat_model()


def _active_model(context: ChatContext) -> str:
    """Return the model identifier for this session.

    Preference:
      1. Explicit CHAT_MODEL in session env
      2. Explicit EGREGORE_CHAT_MODEL in process env
      3. Best available remote model (DeepSeek > Claude)
      4. Largest present local GGUF model
    """
    explicit = context.env.get("CHAT_MODEL") or os.environ.get("EGREGORE_CHAT_MODEL")
    if explicit:
        return explicit

    inference_service = _get_inference_service(context)
    if inference_service is not None:
        if "deepseek" in inference_service.clients:
            return "deepseek-reasoner"
        if "anthropic" in inference_service.clients:
            return "claude-3-5-sonnet-20241022"

    return _best_local_gguf_model()


def _any_remote_backend_available(inference_service: Any) -> bool:
    """True if a remote (non-local) backend is registered (Claude, DeepSeek, etc.)."""
    if inference_service is None:
        return False
    return any(name != "local" for name in inference_service.clients)


def _gguf_catalog() -> Any:
    """Return the GGUF catalog, or None if imports fail."""
    try:
        from egregore.infrastructure.gguf_catalog import GGUFCatalog

        return GGUFCatalog()
    except Exception:
        return None


def _list_gguf_models() -> list[str]:
    """Return IDs of registered GGUF models."""
    catalog = _gguf_catalog()
    if catalog is None:
        return []
    try:
        return cast(list[str], catalog.list_models())
    except Exception:
        return []


def _is_gguf_model(model_id: str) -> bool:
    """Check whether a model ID is registered in the GGUF catalog."""
    catalog = _gguf_catalog()
    if catalog is None:
        return False
    try:
        return catalog.get(model_id) is not None
    except Exception:
        return False


def _default_hold_api(**kwargs: Any) -> str:
    """Stub ANCHORUM litigation-hold API until a live backend is configured."""
    return f"hold-{uuid.uuid4().hex[:12]}"


def _repo_root() -> Path:
    """Return the Egregore repo root."""
    return Path(os.environ.get("EGREGORE_REPO_ROOT", "/opt/egregore"))


def _resolve_zarc_path(path_str: str) -> Path:
    """
    Resolve a .zarc path.

    Absolute paths are used as-is. Relative paths are resolved against the
    repo root so that both `tmp/foo.zarc` and `foo.zarc` work naturally.
    """
    p = Path(path_str)
    if p.is_absolute():
        return p
    return _repo_root() / p


def parse_command(message: str) -> tuple[str, list[str]]:  # noqa: C901
    """
    Parse a chat message into a command and arguments.

    Supports explicit slash commands and a few natural-language patterns.
    Plain text is routed to the AI chat path (/ask) by default.
    """
    text = message.strip()
    if not text:
        return ("help", [])

    # Explicit slash command
    if text.startswith("/"):
        parts = text[1:].split()
        return (parts[0].lower(), parts[1:])

    # Natural-language patterns (best-effort)
    lowered = text.lower()
    if lowered.startswith(
        ("run integrity", "integrity check", "anchorum check", "system check")
    ):
        return ("integrity", [])
    if lowered.startswith(("ingest ", "load ", "import ")):
        rest = text.split(None, 1)[1]
        return ("ingest", rest.split())
    if lowered.startswith(("compare ", "diff ")):
        rest = text.split(None, 1)[1]
        return ("compare", rest.split())
    if lowered.startswith(("hold ", "litigation hold ", "preserve ")):
        rest = text.split(None, 1)[1]
        return ("hold", rest.split())
    if lowered.startswith(("model ", "use model ", "switch model ")):
        rest = text.split(None, 1)[1]
        return ("model", rest.split())
    if lowered.startswith(("agent ", "run agent ", "dispatch ")):
        rest = text.split(None, 1)[1]
        return ("agent", rest.split())
    if lowered.startswith(("legal ", "dossier legal ")):
        rest = text.split(None, 1)[1]
        return ("legal", rest.split())
    if lowered in {"help", "?", "commands"}:
        return ("help", [])

    # Default: plain text is a chat question.
    return ("ask", [text])


def _require_privilege(context: ChatContext, command: str) -> dict[str, Any] | None:
    """Check CHAT_ADMIN privilege using the persistent identity if available."""
    svc = _permission_service()
    check = svc.can(_identity_from_context(context), Action.CHAT_ADMIN)
    if not check.ok:
        return {
            "type": "chat",
            "command": command,
            "ok": False,
            "summary": f"Command `/{command}` requires admin role. Reason: {check.reason}.",
            "detail": None,
        }
    return None


def _require_action(
    context: ChatContext, command: str, action: str
) -> dict[str, Any] | None:
    """Check a generic chat action permission."""
    svc = _permission_service()
    check = svc.can(_identity_from_context(context), action)
    if not check.ok:
        return _deny(
            f"Command `/{command}` is not allowed. Reason: {check.reason}.", command
        )
    return None


def _format_ingest(report: Any) -> dict[str, Any]:
    batch = report.batch_ingested if report.batch_ingested is not None else "unknown"
    summary = f"Ingested {batch} records from `{report.zarc_path}`."
    if report.last_n_requested:
        summary += f" (last {report.last_n_requested} requested)"
    return {
        "type": "chat",
        "command": "ingest",
        "ok": True,
        "summary": summary,
        "detail": {
            "zarc_path": report.zarc_path,
            "last_n_requested": report.last_n_requested,
            "batch_ingested": report.batch_ingested,
            "record_count": len(report.records),
            "verify_chain_ok": report.verify_chain_ok,
        },
        "suggestion": "Run `/compare <left.zarc> <right.zarc>` to verify against another archive.",
    }


def _format_compare(comparison: Any) -> dict[str, Any]:
    verdict = comparison.verdict
    ok = verdict in {"MATCH", "unknown"}
    summary = f"Comparison verdict: **{verdict}**."
    delta = comparison.deltas
    if delta and delta.get("tail_differing_indices_count") is not None:
        diff_count = delta["tail_differing_indices_count"]
        summary += f" Tail differences: {diff_count}."
    return {
        "type": "chat",
        "command": "compare",
        "ok": ok,
        "summary": summary,
        "detail": {
            "verdict": verdict,
            "left": {
                "zarc_path": comparison.left.zarc_path,
                "batch_ingested": comparison.left.batch_ingested,
            },
            "right": {
                "zarc_path": comparison.right.zarc_path,
                "batch_ingested": comparison.right.batch_ingested,
            },
            "deltas": delta,
        },
        "suggestion": "If DIFF or FAIL_CHAIN_VERIFICATION, inspect the .zarc provenance chain.",
    }


def _format_integrity(report: dict[str, Any]) -> dict[str, Any]:
    status = report.get("status", "UNKNOWN")
    checks = report.get("checks", {})
    errors = report.get("errors", [])
    pass_count = sum(1 for v in checks.values() if v in {"PASS", "HEALTHY"})
    [k for k, v in checks.items() if v not in {"PASS", "HEALTHY", "SKIP"}]
    summary = f"Integrity gate status: **{status}**. {pass_count}/{len(checks)} checks passed."
    if errors:
        summary += f" {len(errors)} issue(s) found."
    return {
        "type": "chat",
        "command": "integrity",
        "ok": status == "PASS",
        "summary": summary,
        "detail": {"status": status, "checks": checks, "errors": errors},
        "suggestion": "Review failures in the detail panel or run `/hold <case_id> <reason>` to preserve state.",
    }


def _format_hold(hold_id: str, case_id: str, reason: str) -> dict[str, Any]:
    return {
        "type": "chat",
        "command": "hold",
        "ok": True,
        "summary": f"Litigation hold created for case `{case_id}`.",
        "detail": {"hold_id": hold_id, "case_id": case_id, "reason": reason},
        "suggestion": "Hold ID recorded. Export to ANCHORUM when the live backend is configured.",
    }


def _format_dossier(ack: CommandAck) -> dict[str, Any]:
    data = ack.result.data if ack.result else {}
    return {
        "type": "chat",
        "command": "dossier",
        "ok": ack.http_status == 200,
        "summary": "Dossier generated.",
        "detail": {
            "http_status": ack.http_status,
            "version_id": ack.result.version_id if ack.result else None,
            "version_number": ack.result.version_number if ack.result else None,
            "data": data,
            "outbox_ids": ack.outbox_ids,
        },
        "suggestion": "Try `/ingest`, `/compare`, or `/integrity` to work with ANCHORUM.",
    }


def _format_ask(result: Any) -> dict[str, Any]:
    """Render either a ChatInferenceResult (legacy GGUF) or ChatResponse (multi-backend)."""
    # Multi-backend path (ChatResponse from InferenceService)
    if hasattr(result, "message"):
        if result.message:
            return {
                "type": "chat",
                "command": "ask",
                "ok": True,
                "summary": result.message.content,
                "detail": {
                    "model": result.model,
                    "model_id": result.model,
                    "usage": result.usage,
                    "tokens_generated": result.usage.get("completion_tokens", 0),
                    "finish_reason": result.finish_reason,
                    "m1_passed": result.m1_passed,
                    "m2_passed": result.m2_passed,
                    "m3_passed": result.m3_passed,
                    "m4_passed": result.m4_passed,
                    "inference_id": result.inference_id,
                },
                "suggestion": "Try `/ingest`, `/compare`, or `/integrity` to work with ANCHORUM.",
            }
        return {
            "type": "chat",
            "command": "ask",
            "ok": False,
            "summary": "AI inference returned an empty response.",
            "detail": {"model": result.model},
        }

    # Legacy GGUF path (ChatInferenceResult)
    if result.ok:
        return {
            "type": "chat",
            "command": "ask",
            "ok": True,
            "summary": result.text,
            "detail": {
                "model_id": result.model_id,
                "tokens_generated": result.tokens_generated,
                "latency_ms": round(result.latency_ms, 2),
                "dt_consumed": result.dt_consumed,
                "placement": result.placement_reason,
                "model_hash": result.model_hash,
            },
            "suggestion": "Try `/ingest`, `/compare`, or `/integrity` to work with ANCHORUM.",
        }
    return {
        "type": "chat",
        "command": "ask",
        "ok": False,
        "summary": f"AI inference failed: {result.error}",
        "detail": {"model_id": result.model_id, "placement": result.placement_reason},
    }


def _format_help(context: ChatContext | None = None) -> dict[str, Any]:
    from egregore.application.chat_inference_orchestrator import (
        ChatInferenceOrchestrator,
    )

    gguf_available = ChatInferenceOrchestrator().is_available()
    inference_service = _get_inference_service(context) if context else None
    remote_available = _any_remote_backend_available(inference_service)
    ai_available = gguf_available or remote_available

    commands = [
        {"cmd": "/help", "desc": "Show this help."},
        {"cmd": "/ask <prompt>", "desc": "Ask the active AI model."},
        {"cmd": "/model", "desc": "Show the active model and available backends."},
        {
            "cmd": "/model <name>",
            "desc": "Switch to a model (e.g. qwen2.5-1.5b-instruct, kimi-k2-base, deepseek-chat).",
        },
        {"cmd": "/agents", "desc": "List discovered CLI agents."},
        {
            "cmd": "/agent <name> <instruction>",
            "desc": "Dispatch an instruction to a CLI agent (admin/operator only).",
        },
        {"cmd": "/models list", "desc": "List registered Egregore GGUF models."},
        {
            "cmd": "/models verify",
            "desc": "Verify GGUF model files against the catalog.",
        },
        {
            "cmd": "/ingest <zarc_path> [last_n=100]",
            "desc": "Ingest the tail of a .zarc archive into ANCHORUM.",
        },
        {
            "cmd": "/compare <left.zarc> <right.zarc> [max_tail=50]",
            "desc": "Compare two .zarc ingest runs.",
        },
        {"cmd": "/integrity", "desc": "Run the ANCHORUM phase-1 integrity gate."},
        {
            "cmd": "/hold <case_id> <reason>",
            "desc": "Trigger a litigation hold (admin/operator only).",
        },
        {"cmd": "/dossier <prompt>", "desc": "Generate a deterministic dossier."},
        {
            "cmd": "/legal <question>",
            "desc": "Ask a question against the Legal Dossier RAG knowledge base.",
        },
    ]
    summary = (
        "Available commands. Plain text is automatically routed to the active AI model."
    )
    if ai_available:
        summary += " Use `/model` to see local GGUF and registered API models."
    return {
        "type": "chat",
        "command": "help",
        "ok": True,
        "summary": summary,
        "detail": {"commands": commands},
    }


def _cmd_ingest(args: list[str], context: ChatContext) -> dict[str, Any]:
    auth_error = _require_privilege(context, "ingest")
    if auth_error:
        return auth_error

    if not args:
        return {
            "type": "chat",
            "command": "ingest",
            "ok": False,
            "summary": "Usage: `/ingest <zarc_path> [last_n=100]`",
            "detail": None,
        }

    zarc_path = _resolve_zarc_path(args[0])
    last_n = 100
    if len(args) >= 2:
        try:
            last_n = int(args[1])
        except ValueError:
            return {
                "type": "chat",
                "command": "ingest",
                "ok": False,
                "summary": "`last_n` must be an integer.",
                "detail": None,
            }

    from egregore.governance.anchorum_ingest_runner import run_anchorum_bridge_ingest

    try:
        report = run_anchorum_bridge_ingest(zarc_path=zarc_path, last_n=last_n)
        return _format_ingest(report)
    except Exception as exc:
        return {
            "type": "chat",
            "command": "ingest",
            "ok": False,
            "summary": f"Ingest failed: {exc}",
            "detail": {"zarc_path": str(zarc_path), "last_n": last_n},
        }


def _cmd_compare(args: list[str], context: ChatContext) -> dict[str, Any]:
    auth_error = _require_privilege(context, "compare")
    if auth_error:
        return auth_error

    if len(args) < 2:
        return {
            "type": "chat",
            "command": "compare",
            "ok": False,
            "summary": "Usage: `/compare <left.zarc> <right.zarc> [max_tail=50]`",
            "detail": None,
        }

    left_path = _resolve_zarc_path(args[0])
    right_path = _resolve_zarc_path(args[1])
    max_tail = 50
    if len(args) >= 3:
        try:
            max_tail = int(args[2])
        except ValueError:
            return {
                "type": "chat",
                "command": "compare",
                "ok": False,
                "summary": "`max_tail` must be an integer.",
                "detail": None,
            }

    from egregore.governance.anchorum_ingest_comparator import compare_anchorum_ingests
    from egregore.governance.anchorum_ingest_runner import run_anchorum_bridge_ingest

    try:
        left = run_anchorum_bridge_ingest(zarc_path=left_path, last_n=max_tail)
        right = run_anchorum_bridge_ingest(zarc_path=right_path, last_n=max_tail)
        comparison = compare_anchorum_ingests(left=left, right=right, max_tail=max_tail)
        return _format_compare(comparison)
    except Exception as exc:
        return {
            "type": "chat",
            "command": "compare",
            "ok": False,
            "summary": f"Compare failed: {exc}",
            "detail": {"left": str(left_path), "right": str(right_path)},
        }


def _cmd_integrity(args: list[str], context: ChatContext) -> dict[str, Any]:
    auth_error = _require_privilege(context, "integrity")
    if auth_error:
        return auth_error

    from egregore.governance.anchorum_integrity_gate import (
        AnchorumIntegrityFailure,
        run_anchorum_check,
    )

    try:
        report = run_anchorum_check()
        return _format_integrity(report)
    except AnchorumIntegrityFailure as exc:
        report = getattr(
            exc, "report", {"status": "FAIL", "checks": {}, "errors": [str(exc)]}
        )
        return _format_integrity(report)
    except Exception as exc:
        return {
            "type": "chat",
            "command": "integrity",
            "ok": False,
            "summary": f"Integrity check could not run: {exc}",
            "detail": None,
        }


def _cmd_hold(args: list[str], context: ChatContext) -> dict[str, Any]:
    auth_error = _require_privilege(context, "hold")
    if auth_error:
        return auth_error

    if len(args) < 2:
        return {
            "type": "chat",
            "command": "hold",
            "ok": False,
            "summary": "Usage: `/hold <case_id> <reason>`",
            "detail": None,
        }

    case_id = args[0]
    reason = " ".join(args[1:])

    from egregore.governance.litigation_hold import LitigationHoldTrigger

    try:
        trigger = LitigationHoldTrigger(anchorum_hold_api=_default_hold_api)
        hold_id = trigger.trigger(
            case_id=case_id, scope=["egregore-chat"], reason=reason
        )
        return _format_hold(hold_id, case_id, reason)
    except Exception as exc:
        return {
            "type": "chat",
            "command": "hold",
            "ok": False,
            "summary": f"Hold failed: {exc}",
            "detail": {"case_id": case_id, "reason": reason},
        }


def _cmd_dossier(args: list[str], context: ChatContext) -> dict[str, Any]:
    auth_error = _require_action(context, "dossier", Action.CHAT_DOSSIER)
    if auth_error:
        return auth_error
    prompt = " ".join(args) if args else "hello"
    req = DossierGenerateRequest(
        organization_id="anchorum-chat",
        case_id=context.session_id,
        actor_id=context.user_id or "chat_user",
        input_fingerprint=sha256_hex(prompt.encode("utf-8")),
        engine_version="chat_v1",
        policy_version="chat_policy_v1",
        input_payload={"input": prompt},
        causality_id=f"chat_{context.session_id}",
        request_id=None,
        timestamp_ns=None,
    )
    ack: CommandAck = generate_v1_dossier(request=req)
    return _format_dossier(ack)


def _cmd_model(args: list[str], context: ChatContext) -> dict[str, Any]:
    inference_service = _get_inference_service(context)

    if not args:
        current = _active_model(context)
        available: list[dict[str, Any]] = []
        if inference_service is not None:
            for name, client in inference_service.clients.items():
                try:
                    models = client.list_model_names()
                    reachable = True
                except Exception:
                    models = []
                    reachable = False
                available.append(
                    {"backend": name, "models": models, "reachable": reachable}
                )
        # Include native GGUF catalog models as a separate backend list.
        gguf_models = _list_gguf_models()
        if gguf_models:
            available.append(
                {"backend": "gguf", "models": gguf_models, "reachable": True}
            )
        return {
            "type": "chat",
            "command": "model",
            "ok": True,
            "summary": f"Active model: `{current}`",
            "detail": {"active_model": current, "available_backends": available},
            "suggestion": "Switch with `/model <name>` (e.g. `/model qwen2.5-1.5b-instruct`).",
        }

    model_name = args[0]
    if inference_service is not None:
        lower = model_name.lower()
        registered_backends = set(inference_service.clients.keys())
        is_known_prefix = (
            (lower.startswith("deepseek-") and "deepseek" in registered_backends)
            or (lower.startswith("claude-") and "anthropic" in registered_backends)
            or (lower.startswith("kimi-") and "local" in registered_backends)
            or (lower.startswith("local-") and "local" in registered_backends)
        )
        if (
            not is_known_prefix
            and not inference_service.model_exists(model_name)
            and not _is_gguf_model(model_name)
        ):
            try:
                available_models = inference_service.list_models()
            except Exception:
                available_models = []
            return {
                "type": "chat",
                "command": "model",
                "ok": False,
                "summary": f"Model `{model_name}` is not registered.",
                "detail": {"available_models": available_models},
            }

    # Persist the choice in the session env (the dict is mutable even though the dataclass is frozen).
    context.env["CHAT_MODEL"] = model_name
    return {
        "type": "chat",
        "command": "model",
        "ok": True,
        "summary": f"Switched to model `{model_name}`.",
        "detail": {"active_model": model_name},
    }


def _cmd_agents(args: list[str], context: ChatContext) -> dict[str, Any]:
    auth_error = _require_action(context, "agents", Action.CHAT_AGENTS)
    if auth_error:
        return auth_error
    registry = _get_agent_registry(context)
    if registry is None:
        return {
            "type": "chat",
            "command": "agents",
            "ok": False,
            "summary": "Agent registry is not configured.",
            "detail": None,
        }

    agents = registry.list_agents()
    if not agents:
        return {
            "type": "chat",
            "command": "agents",
            "ok": True,
            "summary": "No agents discovered.",
            "detail": {"agent_dir": str(registry.agent_dir), "agents": []},
            "suggestion": "Add executable files to the agent directory and run `/agents` again.",
        }

    entries = [
        {
            "name": a.name,
            "description": a.description,
            "timeout": a.timeout,
            "allowed_roles": sorted(a.allowed_roles),
        }
        for a in agents
    ]
    return {
        "type": "chat",
        "command": "agents",
        "ok": True,
        "summary": f"Discovered {len(agents)} agent(s).",
        "detail": {"agent_dir": str(registry.agent_dir), "agents": entries},
        "suggestion": "Run `/agent <name> <instruction>` to dispatch one.",
    }


def _cmd_agent(args: list[str], context: ChatContext) -> dict[str, Any]:
    auth_error = _require_privilege(context, "agent")
    if auth_error:
        return auth_error

    if not args:
        return {
            "type": "chat",
            "command": "agent",
            "ok": False,
            "summary": "Usage: `/agent <name> <instruction>`",
            "detail": None,
        }

    agent_name = args[0]
    instruction = " ".join(args[1:]) if len(args) > 1 else ""

    registry = _get_agent_registry(context)
    if registry is None:
        return {
            "type": "chat",
            "command": "agent",
            "ok": False,
            "summary": "Agent registry is not configured.",
            "detail": None,
        }

    spec = registry.get(agent_name)
    if spec is None:
        available = [a.name for a in registry.list_agents()]
        return {
            "type": "chat",
            "command": "agent",
            "ok": False,
            "summary": f"Agent `{agent_name}` not found.",
            "detail": {"available_agents": available},
        }

    if context.role not in spec.allowed_roles:
        return {
            "type": "chat",
            "command": "agent",
            "ok": False,
            "summary": f"Agent `{agent_name}` requires one of these roles: {', '.join(sorted(spec.allowed_roles))}. Your role is `{context.role}`.",
            "detail": None,
        }

    runner = AgentRunner()
    agent_context = {
        "session_id": context.session_id,
        "user_id": context.user_id,
        "role": context.role,
        "agent_name": agent_name,
    }
    result = runner.run(spec, instruction, agent_context)

    if result.ok:
        return {
            "type": "chat",
            "command": "agent",
            "ok": True,
            "summary": result.stdout or "(agent produced no output)",
            "detail": {
                "agent": agent_name,
                "returncode": result.returncode,
                "duration_ms": round(result.duration_ms, 2),
                "stderr": result.stderr,
            },
            "suggestion": "Run `/agents` to see other available agents.",
        }

    summary = result.error or f"Agent exited with code {result.returncode}"
    if result.stderr:
        summary += f"\n{result.stderr}"
    return {
        "type": "chat",
        "command": "agent",
        "ok": False,
        "summary": summary,
        "detail": {
            "agent": agent_name,
            "returncode": result.returncode,
            "duration_ms": round(result.duration_ms, 2),
            "timed_out": result.timed_out,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    }


def _load_history(context: ChatContext) -> list[dict[str, str]]:
    """Load persisted conversation history for this session."""
    raw = context.env.get(_CHAT_HISTORY_KEY, "[]")
    try:
        history = canonical_loads(raw)
        if not isinstance(history, list):
            return []
        return history
    except Exception:
        return []


def _save_history(context: ChatContext, history: list[dict[str, str]]) -> None:
    """Persist conversation history, capped to a maximum number of turns."""
    max_messages = _CHAT_HISTORY_MAX_TURNS * 2
    if len(history) > max_messages:
        history = history[-max_messages:]
    context.env[_CHAT_HISTORY_KEY] = canonical_dumps(history)


def _append_history_turn(context: ChatContext, role: str, content: str) -> None:
    """Append a single turn to the session history."""
    history = _load_history(context)
    history.append({"role": role, "content": content})
    _save_history(context, history)


def _build_chat_messages(
    system_message: str,
    user_prompt: str,
    history: list[dict[str, str]],
) -> list[ChatMessage]:
    """Assemble system + history + current user messages."""
    messages: list[ChatMessage] = [ChatMessage(role="system", content=system_message)]
    for turn in history:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append(ChatMessage(role=turn["role"], content=turn["content"]))
    messages.append(ChatMessage(role="user", content=user_prompt))
    return messages


def _cmd_legal(args: list[str], context: ChatContext) -> dict[str, Any]:  # noqa: C901
    auth_error = _require_action(context, "legal", Action.CHAT_ASK)
    if auth_error:
        return auth_error

    if not args:
        return {
            "type": "chat",
            "command": "legal",
            "ok": False,
            "summary": "Usage: `/legal <question>`",
            "detail": None,
        }

    user_query = " ".join(args)
    try:
        rag_response = query_rag(RAGQuery(query=user_query, top_k=8))
    except Exception as exc:
        return {
            "type": "chat",
            "command": "legal",
            "ok": False,
            "summary": f"RAG query failed: {exc}",
            "detail": None,
        }

    results = rag_response.results
    if not results:
        return {
            "type": "chat",
            "command": "legal",
            "ok": True,
            "summary": "No relevant documents were found in the Legal Dossier knowledge base.",
            "detail": {"query": user_query, "result_count": 0},
        }

    evidence_blocks: list[str] = []
    sources: list[str] = []
    for idx, result in enumerate(results, start=1):
        metadata = result.get("metadata") or {}
        source = metadata.get("source", "unknown")
        sources.append(source)
        evidence_blocks.append(
            f"[{idx}] Source: {source}\n{result.get('document', '')}"
        )
    evidence = "\n\n".join(evidence_blocks)

    system_message = (
        "You are a research assistant for the Legal Dossier. "
        "Answer the user's question using ONLY the evidence excerpts below. "
        "Cite facts with bracket numbers like [1], [2], etc. "
        "If the evidence does not contain the answer, say so clearly.\n\n"
        f"EVIDENCE:\n{evidence}"
    )

    synthesis: str | None = None
    active_model = _active_model(context)
    inference_service = _get_inference_service(context)
    try:
        if _is_gguf_model(active_model):
            from egregore.application.chat_inference_orchestrator import (
                ChatInferenceOrchestrator,
            )

            orchestrator = ChatInferenceOrchestrator()
            messages: list[dict[str, str]] = [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_query},
            ]
            result = orchestrator.chat(messages, model_id=active_model)
            if result.ok:
                synthesis = result.text
        elif inference_service is not None:
            request = ChatRequest(
                model=active_model,
                messages=_build_chat_messages(system_message, user_query, []),
                mode=InferenceMode.DETERMINISTIC,
                max_tokens=2048,
                seed=42,
            )
            result = inference_service.execute(request, node_id="pioneer1")
            if result.message:
                synthesis = result.message.content
        else:
            # Legacy fallback: ask the native GGUF host if available.
            from egregore.application.chat_inference_orchestrator import (
                ChatInferenceOrchestrator,
            )

            orchestrator = ChatInferenceOrchestrator()
            messages = [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_query},
            ]
            result = orchestrator.chat(messages, model_id=active_model)
            if result.ok:
                synthesis = result.text
    except Exception:
        synthesis = None

    if synthesis:
        summary = synthesis
    else:
        summary = (
            f"Found {len(results)} relevant excerpt(s) for your question. "
            "An AI synthesis is not available right now, so the raw evidence is shown below:\n\n"
            f"{evidence}"
        )

    return {
        "type": "chat",
        "command": "legal",
        "ok": True,
        "summary": summary,
        "detail": {
            "query": user_query,
            "result_count": len(results),
            "sources": sources,
            "synthesized": synthesis is not None,
        },
    }


def _cmd_ask(args: list[str], context: ChatContext) -> dict[str, Any]:
    user_prompt = " ".join(args) if args else "hello"

    inference_service = _get_inference_service(context)
    active_model = _active_model(context)
    system_message = (
        "You are Egregore, a deterministic AI assistant. Be concise and helpful."
    )
    history = _load_history(context)

    # Native GGUF path: use Egregore model host for catalog-registered GGUF models.
    if _is_gguf_model(active_model):
        from egregore.application.chat_inference_orchestrator import (
            ChatInferenceOrchestrator,
        )

        orchestrator = ChatInferenceOrchestrator()
        messages: list[dict[str, str]] = [{"role": "system", "content": system_message}]
        for turn in history:
            messages.append(turn)
        messages.append({"role": "user", "content": user_prompt})
        result = orchestrator.chat(messages, model_id=active_model)
        _append_history_turn(context, "user", user_prompt)
        _append_history_turn(context, "assistant", result.text)
        return _format_ask(result)

    # Multi-backend path: use InferenceService (native Coder, Claude, DeepSeek, local HF)
    if inference_service is not None:
        request = ChatRequest(
            model=active_model,
            messages=_build_chat_messages(system_message, user_prompt, history),
            mode=InferenceMode.DETERMINISTIC,
            max_tokens=2048,
            seed=42,
        )
        try:
            result = inference_service.execute(request, node_id="pioneer1")
            _append_history_turn(context, "user", user_prompt)
            _append_history_turn(context, "assistant", result.message.content)
            return _format_ask(result)
        except Exception as exc:
            return {
                "type": "chat",
                "command": "ask",
                "ok": False,
                "summary": f"Inference service error: {exc}",
                "detail": {"model": active_model},
            }

    # Legacy fallback: native GGUF model host without explicit model selection.
    prompt = (
        "<|im_start|>system\n"
        f"{system_message}\n"
        "<|im_end|>\n"
        f"<|im_start|>user\n{user_prompt}\n<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    from egregore.application.chat_inference_orchestrator import (
        ChatInferenceOrchestrator,
    )

    orchestrator = ChatInferenceOrchestrator()
    result = orchestrator.ask(prompt)
    _append_history_turn(context, "user", user_prompt)
    _append_history_turn(context, "assistant", result.text)
    return _format_ask(result)


def _cmd_models(args: list[str], context: ChatContext) -> dict[str, Any]:
    auth_error = _require_privilege(context, "models")
    if auth_error:
        return auth_error

    from egregore.infrastructure.gguf_catalog import GGUFCatalog

    catalog = GGUFCatalog()
    subcommand = args[0].lower() if args else "list"

    if subcommand == "list":
        from egregore.infrastructure.gguf_catalog import GGUF_ROOT

        entries = []
        for model_id, entry in catalog._entries.items():
            path = GGUF_ROOT / entry.tier / entry.filename
            entries.append(
                {
                    "model_id": model_id,
                    "tier": entry.tier,
                    "quantization": entry.quantization,
                    "parameters": entry.parameters,
                    "size_bytes": entry.size_bytes,
                    "present": path.exists(),
                }
            )
        return {
            "type": "chat",
            "command": "models",
            "ok": True,
            "summary": f"Egregore catalog: {len(entries)} model(s) registered.",
            "detail": {"models": entries},
        }

    if subcommand == "verify":
        results = catalog.verify_all()
        ok = all(status == "VERIFIED" for status in results.values())
        return {
            "type": "chat",
            "command": "models",
            "ok": ok,
            "summary": f"Verification complete. {sum(1 for v in results.values() if v == 'VERIFIED')}/{len(results)} verified.",
            "detail": {"results": results},
        }

    return {
        "type": "chat",
        "command": "models",
        "ok": False,
        "summary": "Usage: `/models list` or `/models verify`",
        "detail": None,
    }


_COMMAND_HANDLERS: dict[str, Callable[[list[str], ChatContext], dict[str, Any]]] = {
    "help": lambda _args, ctx: _format_help(ctx),
    "ask": _cmd_ask,
    "model": _cmd_model,
    "agents": _cmd_agents,
    "agent": _cmd_agent,
    "models": _cmd_models,
    "ingest": _cmd_ingest,
    "compare": _cmd_compare,
    "integrity": _cmd_integrity,
    "check": _cmd_integrity,
    "hold": _cmd_hold,
    "dossier": _cmd_dossier,
    "legal": _cmd_legal,
}


def execute_message(message: str, context: ChatContext) -> dict[str, Any]:
    """Parse a message and execute the corresponding command."""
    from egregore.application.chat_inference_orchestrator import (
        ChatInferenceOrchestrator,
    )

    inference_service = _get_inference_service(context)
    _any_remote_backend_available(inference_service)
    ChatInferenceOrchestrator().is_available()
    command, args = parse_command(message)
    handler = _COMMAND_HANDLERS.get(command, _cmd_dossier)
    return handler(args, context)
