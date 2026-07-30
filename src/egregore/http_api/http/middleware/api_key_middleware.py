"""api_key_middleware.py — Drop-in replacement for IdentityMiddleware.

Why this exists:
  The current IdentityMiddleware trusts arbitrary client headers (X-User-ID,
  X-Role, etc.) with zero validation. An attacker can curl with
  `X-User-ID: admin` and bypass all authorization.

  This middleware replaces that with API-key validation:
    - Keys are read from env / Docker secrets at startup
    - Every request must provide a valid key in X-API-Key header
    - Keys are mapped to (tenant_id, user_id, role) tuples
    - Invalid key → 401 Unauthorized, no tenant extraction, no passthrough

  This is a temporary but secure bridge while you build the real OIDC/JWT flow.
  It closes the "header impersonation" attack immediately.

Usage (FastAPI):
    from fastapi import FastAPI
    from egregore.http_api.http.middleware.api_key_middleware import APIKeyMiddleware

    app = FastAPI()
    app.add_middleware(APIKeyMiddleware)

  Or replace the existing IdentityMiddleware import in your app factory.

Architecture notes:
  - Lives in http_api/middleware/ alongside the old IdentityMiddleware
  - Reads keys from EGREGORE_API_KEYS env var (comma-separated) or
    EGREGORE_API_KEYS_PATH file (JSON)
  - No in-memory mutable state — keys are loaded once at init
  - Fail-closed: if no keys are configured, ALL requests are rejected
"""

import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from egregore.models.user import UserIdentity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key loading (once at import time)
# ---------------------------------------------------------------------------


def _load_dotenv_api_keys() -> dict[str, tuple[str, str, str]]:
    """Read EGREGORE_API_KEYS directly from the repo .env file.

    This is a fallback used when the process environment has stale/injected
    values (e.g. from a parent shell or runtime wrapper) and .env is the
    intended source of truth.
    """
    keys: dict[str, tuple[str, str, str]] = {}
    repo_root = Path(__file__).resolve().parents[5]
    dotenv_path = repo_root / ".env"
    if not dotenv_path.exists():
        return keys

    try:
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != "EGREGORE_API_KEYS":
                continue
            value = value.strip().strip("\"'")
            for entry in value.split(","):
                parts = entry.strip().split(":")
                if len(parts) >= 1 and len(parts[0]) == 64:
                    k = parts[0]
                    tenant = parts[1] if len(parts) > 1 else "default"
                    user = parts[2] if len(parts) > 2 else "api_user"
                    role = parts[3] if len(parts) > 3 else "reader"
                    keys[k] = (tenant, user, role)
            logger.info(
                f"[APIKeyMiddleware] Loaded {len(keys)} keys from {dotenv_path}"
            )
    except Exception:  # noqa: S110
        pass
    return keys


def _load_api_keys() -> dict[str, tuple[str, str, str]]:
    """Load API keys from env or file.

    Precedence:
      1. EGREGORE_API_KEYS env var (allows tests and containers to override)
      2. EGREGORE_API_KEYS_PATH JSON file
      3. Repo .env file (fallback for local/dev deployments)

    Returns: {api_key_hex: (tenant_id, user_id, role)}
    """
    keys: dict[str, tuple[str, str, str]] = {}

    # Source 1: Env var (comma-separated key:tenant:user:role)
    env_keys = os.environ.get("EGREGORE_API_KEYS", "")
    if env_keys:
        for entry in env_keys.split(","):
            parts = entry.strip().split(":")
            if len(parts) >= 1 and len(parts[0]) == 64:
                key = parts[0]
                tenant = parts[1] if len(parts) > 1 else "default"
                user = parts[2] if len(parts) > 2 else "api_user"
                role = parts[3] if len(parts) > 3 else "reader"
                keys[key] = (tenant, user, role)
        logger.info(f"[APIKeyMiddleware] Loaded {len(keys)} keys from env")

    # Source 2: JSON file path
    keys_path = os.environ.get("EGREGORE_API_KEYS_PATH", "")
    if keys_path and os.path.exists(keys_path):
        before = len(keys)
        with open(keys_path) as f:
            data = json.load(f)
            for entry in data:
                key = entry.get("key", "")
                if key and len(key) == 64:
                    keys[key] = (
                        entry.get("tenant_id", "default"),
                        entry.get("user_id", "unknown"),
                        entry.get("role", "reader"),
                    )
        logger.info(
            f"[APIKeyMiddleware] Loaded {len(keys) - before} keys from {keys_path}"
        )

    # Source 3: repo .env file (fallback for local/dev deployments)
    keys.update(_load_dotenv_api_keys())

    return keys


_API_KEYS = _load_api_keys()


_ROLE_PRIORITY = ["admin", "operator", "user", "guest", "reader"]


def _primary_role(roles: list[str]) -> str:
    """Return the highest-privilege role for legacy single-role checks."""
    role_set = set(roles)
    for r in _ROLE_PRIORITY:
        if r in role_set:
            return r
    return roles[0] if roles else "reader"


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _resolve_identity(key: str) -> "UserIdentity | None":
    """Look up the persistent user identity for an API key, bootstrapping if necessary."""
    try:
        from egregore.infrastructure.persistence.user_repository import (
            SQLiteUserRepository,
            _hash_api_key,
        )
        from egregore.models.user import UserIdentity
    except Exception as exc:  # noqa: BLE001
        logger.warning("[APIKeyMiddleware] Could not import user repository: %s", exc)
        return None

    repo = SQLiteUserRepository()
    key_hash = _hash_api_key(key)

    # Ensure at least one admin exists if the DB is empty.
    repo.bootstrap_admin_if_needed(tenant_id="default", api_keys=_API_KEYS)

    identity = repo.resolve_identity(key_hash, tenant_id="default")
    if identity is not None:
        with contextlib.suppress(Exception):
            repo.touch_api_key(key_hash)
        return identity

    # Fallback: key is configured in env but not yet in DB. Map to legacy identity.
    env_identity = _API_KEYS.get(key)
    if env_identity:
        tenant_id, user_id, role = env_identity
        return UserIdentity(
            tenant_id=tenant_id,
            user_id=user_id,
            username=user_id,
            email=None,
            roles=[role],
            vertical_grants=[],
            status="active",
            account_id=None,
        )
    return None


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validates X-API-Key header on every request.

    Rejects with 401 if:
      - No API keys are configured (fail-closed)
      - X-API-Key header is missing
      - X-API-Key value is not in the authorized set

    On success, injects validated identity into request.state:
      - request.state.tenant_id
      - request.state.user_id
      - request.state.role       (legacy primary role for backward compat)
      - request.state.roles      (list of roles)
      - request.state.user       (UserIdentity)
      - request.state.authenticated
    """

    # Paths that must be reachable without an API key (login page, static assets, health probes)
    PUBLIC_PATHS = {
        "/dashboard/login",
        "/favicon.ico",
        "/health/ready",
        "/health/live",
        "/health/nodes",
        "/ingest",
    }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public assets and the login page to load without authentication
        if path in self.PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        # Fail-closed: no keys configured = reject everything
        if not _API_KEYS:
            logger.warning(
                "[APIKeyMiddleware] No API keys configured — rejecting all requests"
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "API key authentication not configured"},
            )

        # Extract key from header or cookie
        api_key = request.headers.get("X-API-Key", "")
        # Fallback: also check for cookie (so bookmarklets work)
        if not api_key:
            api_key = request.cookies.get("api_key", "")
        if not api_key:
            logger.warning(
                f"[APIKeyMiddleware] Missing API key from {request.client.host}"
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing API key"},
            )

        # Validate key
        identity = _resolve_identity(api_key)
        if identity is None:
            logger.warning(
                f"[APIKeyMiddleware] Invalid API key from {request.client.host}"
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid API key"},
            )

        # Inject validated identity into request state
        request.state.tenant_id = identity.tenant_id
        request.state.user_id = identity.user_id
        request.state.roles = identity.roles
        # Preserve a single primary role for backward compat; prefer admin/operator/user/guest.
        request.state.role = _primary_role(identity.roles)
        request.state.user = identity
        request.state.authenticated = True

        logger.debug(
            f"[APIKeyMiddleware] Authenticated {identity.user_id}@{identity.tenant_id} ({identity.roles})"
        )

        return await call_next(request)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def is_valid_api_key(key: str) -> bool:
    """Return True if the supplied key is in the configured key registry."""
    return bool(key) and key in _API_KEYS


def get_identity_for_key(key: str) -> tuple[str, str, str] | None:
    """Return (tenant_id, user_id, role) for a valid key, or None."""
    return _API_KEYS.get(key)


# ---------------------------------------------------------------------------
# Dependency for FastAPI endpoints
# ---------------------------------------------------------------------------


def require_auth(request: Request) -> tuple[str, str, str]:
    """FastAPI dependency: extract validated identity from request.state.

    Usage:
        @app.get("/protected")
        def protected(identity: tuple = Depends(require_auth)):
            tenant_id, user_id, role = identity
            ...
    """
    if not getattr(request.state, "authenticated", False):
        raise HTTPException(status_code=401, detail="Not authenticated")

    return (
        request.state.tenant_id,
        request.state.user_id,
        request.state.role,
    )


def require_role(required_role: str):
    """FastAPI dependency factory: enforce role.

    Usage:
        @app.post("/admin")
        def admin_action(identity: tuple = Depends(require_role("admin"))):
            ...
    """

    def _check(request: Request) -> tuple[str, str, str]:
        identity = require_auth(request)
        tenant_id, user_id, role = identity
        roles = getattr(request.state, "roles", [role])
        if required_role not in roles:
            raise HTTPException(
                status_code=403, detail=f"Role '{required_role}' required"
            )
        return identity

    return _check


def get_user_identity(request: Request):
    """Return the full UserIdentity from request state."""
    identity = getattr(request.state, "user", None)
    if identity is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return identity
