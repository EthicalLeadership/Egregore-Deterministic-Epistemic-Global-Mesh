from __future__ import annotations

from egregore.domain.semantics_models import GenerateDossierCommand
from egregore.interface.semantics_ports import IAuthzProvider
from egregore.models.user import User


class RBACAuthzProvider(IAuthzProvider):
    def __init__(self, users: list[User]):
        self._users = {u.id: u for u in users}

    def authorize_generate(self, *, command: GenerateDossierCommand) -> None:
        # Demo mode: if no users are configured, allow all requests.
        if not self._users:
            return None
        # Example: Only allow users with 'active' status and required role
        user_id = getattr(command, "actor_id", None)
        if not user_id or user_id not in self._users:
            raise PermissionError("User not found")
        user = self._users[user_id]
        if user.status != "active":
            raise PermissionError("User not active")
        # Example: require 'admin' for certain actions (expand as needed)
        required_role = getattr(command, "required_role", None)
        if required_role and required_role not in user.roles:
            raise PermissionError(f"User lacks required role: {required_role}")
        # Add vertical/tenant checks as needed
        # ...
        return None
