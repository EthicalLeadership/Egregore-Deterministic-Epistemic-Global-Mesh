# epistemic marker: provenance / auditability
"""Vertical-aware permission service for Egregore Chat.

Centralizes all authorization decisions so the middleware, HTTP routers,
WebSocket interpreter, and CLI can ask the same questions.
"""

from __future__ import annotations

from dataclasses import dataclass

from egregore.models.user import UserIdentity

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


class Action:
    # Chat commands
    CHAT_ADMIN = "chat:admin"
    CHAT_ASK = "chat:ask"
    CHAT_DOSSIER = "chat:dossier"
    CHAT_MODELS = "chat:models"
    CHAT_AGENTS = "chat:agents"

    # Vertical/cell access
    VERTICAL_READ = "vertical:read"
    VERTICAL_WRITE = "vertical:write"

    # User management
    USER_MANAGE = "user:manage"

    # System controls
    SYSTEM_FREEZE = "system:freeze"


# ---------------------------------------------------------------------------
# Permission service
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionCheck:
    ok: bool
    reason: str = ""


class PermissionService:
    """Answer authorization questions for a resolved UserIdentity."""

    def __init__(self) -> None:
        pass

    def can(  # noqa: C901
        self,
        identity: UserIdentity | None,
        action: str,
        vertical: str | None = None,
    ) -> PermissionCheck:
        if identity is None:
            return PermissionCheck(False, "Not authenticated")
        privileged = {"admin", "operator"}
        if identity.status != "active" and not privileged.intersection(identity.roles):
            return PermissionCheck(False, f"User status is {identity.status}")

        roles = set(identity.roles)

        # Admin / operator bypass
        if "admin" in roles or "operator" in roles:
            return PermissionCheck(True, "admin")

        # User management requires admin
        if action == Action.USER_MANAGE:
            return PermissionCheck(False, "admin role required")

        # System freeze requires admin/operator
        if action == Action.SYSTEM_FREEZE:
            return PermissionCheck(False, "admin role required")

        # Chat admin commands (ingest, compare, integrity, hold, models admin)
        if action == Action.CHAT_ADMIN:
            return PermissionCheck(False, "admin role required")

        # Non-privileged chat
        if action in {
            Action.CHAT_ASK,
            Action.CHAT_DOSSIER,
            Action.CHAT_MODELS,
            Action.CHAT_AGENTS,
        }:
            if "guest" in roles and action != Action.CHAT_ASK:
                # Guests may ask but not generate dossiers/agents/models.
                return PermissionCheck(False, "guests are read-only")
            if "user" in roles or "guest" in roles or "operator" in roles:
                return PermissionCheck(True, "allowed")
            return PermissionCheck(False, "no permitted role")

        # Vertical access
        if action in {Action.VERTICAL_READ, Action.VERTICAL_WRITE}:
            grants = {g.cell_id: g.permission for g in identity.vertical_grants}
            required = "write" if action == Action.VERTICAL_WRITE else "read"
            if vertical is None:
                # Any vertical grant at the required level grants broad access.
                if any(p == "write" or required == "read" for p in grants.values()):
                    return PermissionCheck(True, "vertical grant")
                return PermissionCheck(False, f"no {required} grant on any vertical")
            perm = grants.get(vertical)
            if perm == "write":
                return PermissionCheck(True, "vertical write grant")
            if perm == "read" and required == "read":
                return PermissionCheck(True, "vertical read grant")
            return PermissionCheck(False, f"no {required} grant on {vertical}")

        return PermissionCheck(False, f"unknown action: {action}")

    def require(
        self,
        identity: UserIdentity | None,
        action: str,
        vertical: str | None = None,
    ) -> None:
        check = self.can(identity, action, vertical)
        if not check.ok:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail=check.reason)

    def is_admin(self, identity: UserIdentity | None) -> bool:
        return (
            identity is not None
            and "admin" in identity.roles
            and identity.status == "active"
        )
