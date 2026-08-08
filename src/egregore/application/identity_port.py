"""Identity verification port for execution-boundary checks."""

from __future__ import annotations

from typing import Any


def _identity_to_payload(identity: Any) -> dict[str, Any]:
    return {
        "tenant_id": str(identity.tenant_id),
        "user_id": str(identity.user_id),
        "username": str(identity.username),
        "roles": list(identity.roles),
        "status": str(identity.status),
        "account_id": identity.account_id,
    }


def verify_identity(token: str) -> dict[str, Any]:
    """Verify an identity token and return a normalized caller payload.

    Supported token formats:
    - ``api_key:<raw_api_key>``
    - ``api_key_hash:<sha256_hex>``
    """
    raw = token.strip()
    if not raw:
        raise ValueError("Empty caller identity token")

    if raw.startswith("api_key:"):
        api_key = raw[len("api_key:") :].strip()
        if not api_key:
            raise ValueError("Invalid caller identity token: missing API key")
        from egregore.http_api.http.middleware.api_key_middleware import _resolve_identity

        identity = _resolve_identity(api_key)
        if identity is None:
            raise ValueError("Identity token verification failed")
        return _identity_to_payload(identity)

    if raw.startswith("api_key_hash:"):
        key_hash = raw[len("api_key_hash:") :].strip().lower()
        if len(key_hash) != 64 or any(c not in "0123456789abcdef" for c in key_hash):
            raise ValueError("Invalid caller identity token: malformed key hash")

        from egregore.infrastructure.persistence.user_repository import (
            SQLiteUserRepository,
            get_default_user_repository,
        )

        repo = get_default_user_repository()
        if isinstance(repo, SQLiteUserRepository):
            identity = repo.resolve_identity(key_hash, tenant_id="default")
        else:
            identity = None
        if identity is None:
            raise ValueError("Identity token verification failed")
        return _identity_to_payload(identity)

    raise ValueError("Unsupported caller identity token format")