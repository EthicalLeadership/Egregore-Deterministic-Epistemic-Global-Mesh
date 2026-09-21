"""Typed task intent envelope for agent execution boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from egregore.shared.canonical import canonical_dumps, canonical_loads

ALLOWED_TASK_DOMAINS = frozenset(
    {"code", "infra", "legal", "forensic", "ops", "general"}
)
ALLOWED_OUTPUT_SINKS = frozenset({"chat", "log", "artifact"})
ALLOWED_REQUIRED_PRIVILEGES = frozenset({"read", "operator", "admin"})
ROLE_TO_PRIVILEGE = {
    "reader": "read",
    "read": "read",
    "operator": "operator",
    "admin": "admin",
}
PRIVILEGE_ORDER = {"read": 0, "operator": 1, "admin": 2}


@dataclass(frozen=True, slots=True)
class TaskIntent:
    domain: str
    required_privilege: str
    output_sink: str
    human_approval_required: bool
    instruction: str

    def to_payload(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "required_privilege": self.required_privilege,
            "output_sink": self.output_sink,
            "human_approval_required": self.human_approval_required,
            "instruction": self.instruction,
        }

    def to_json(self) -> str:
        return canonical_dumps(self.to_payload())


class TaskIntentError(ValueError):
    """Raised when a task intent envelope is invalid."""


def highest_privilege_for_roles(roles: list[str]) -> str | None:
    highest: str | None = None
    for role in roles:
        privilege = ROLE_TO_PRIVILEGE.get(role.strip().lower())
        if privilege is None:
            continue
        if highest is None or PRIVILEGE_ORDER[privilege] > PRIVILEGE_ORDER[highest]:
            highest = privilege
    return highest


def identity_satisfies_privilege(identity_payload: object, required_privilege: str) -> bool:
    if not isinstance(identity_payload, dict):
        return False
    roles_value = identity_payload.get("roles")
    status_value = str(identity_payload.get("status", "")).strip().lower()
    if not isinstance(roles_value, list):
        return False
    roles = [str(role) for role in roles_value]
    if status_value != "active":
        return False
    actor_privilege = highest_privilege_for_roles(roles)
    if actor_privilege is None:
        return False
    return PRIVILEGE_ORDER[actor_privilege] >= PRIVILEGE_ORDER[required_privilege]


def validate_task_intent_payload(payload: object) -> TaskIntent:
    if not isinstance(payload, dict):
        raise TaskIntentError("TaskIntent must be a JSON object.")

    required_fields = {
        "domain",
        "required_privilege",
        "output_sink",
        "human_approval_required",
        "instruction",
    }
    missing = sorted(required_fields.difference(payload.keys()))
    if missing:
        raise TaskIntentError(
            f"TaskIntent missing required fields: {', '.join(missing)}"
        )

    if "max_privilege" in payload:
        raise TaskIntentError(
            "TaskIntent field 'max_privilege' is no longer accepted; use 'required_privilege'."
        )

    domain = str(payload.get("domain", "")).strip().lower()
    required_privilege = str(payload.get("required_privilege", "")).strip().lower()
    output_sink = str(payload.get("output_sink", "")).strip().lower()
    human_approval_required_value = payload.get("human_approval_required")
    instruction = str(payload.get("instruction", "")).strip()

    violations: list[str] = []
    if domain not in ALLOWED_TASK_DOMAINS:
        violations.append(f"domain must be one of: {sorted(ALLOWED_TASK_DOMAINS)}")
    if required_privilege not in ALLOWED_REQUIRED_PRIVILEGES:
        violations.append(
            f"required_privilege must be one of: {sorted(ALLOWED_REQUIRED_PRIVILEGES)}"
        )
    if output_sink not in ALLOWED_OUTPUT_SINKS:
        violations.append(
            f"output_sink must be one of: {sorted(ALLOWED_OUTPUT_SINKS)}"
        )
    if not isinstance(human_approval_required_value, bool):
        violations.append("human_approval_required must be a boolean")
    if not instruction:
        violations.append("instruction must be a non-empty string")

    if violations:
        raise TaskIntentError("TaskIntent validation failed: " + "; ".join(violations))

    human_approval_required = cast(bool, human_approval_required_value)

    return TaskIntent(
        domain=domain,
        required_privilege=required_privilege,
        output_sink=output_sink,
        human_approval_required=human_approval_required,
        instruction=instruction,
    )


def parse_task_intent(
    raw_instruction: str,
    *,
    allow_legacy: bool = False,
    legacy_role: str = "operator",
) -> tuple[TaskIntent, str]:
    """Parse a task intent envelope.

    Returns ``(intent, warning)``. When ``allow_legacy`` is true, non-JSON input is
    wrapped into a conservative envelope and returns a deprecation warning.
    """
    stripped = raw_instruction.strip()
    if not stripped:
        raise TaskIntentError("TaskIntent envelope required.")

    try:
        parsed = canonical_loads(stripped)
    except Exception:
        if not allow_legacy:
            raise TaskIntentError("TaskIntent must be valid JSON.") from None
        legacy_privilege = ROLE_TO_PRIVILEGE.get(legacy_role.strip().lower(), "read")
        intent = TaskIntent(
            domain="general",
            required_privilege=legacy_privilege,
            output_sink="chat",
            human_approval_required=True,
            instruction=stripped,
        )
        return intent, (
            "Legacy raw agent instructions are deprecated; send TaskIntent JSON."
        )

    return validate_task_intent_payload(parsed), ""
