"""
api_key_middleware.py — Production‑grade API key authentication.

Replaces IdentityMiddleware with:
  - API keys validated via X-API-Key or Authorization: Bearer
  - Keys loaded from env, JSON file, or .env (no side effects)
  - Fail‑closed: no keys → all protected requests rejected
  - In‑memory identity cache (TTL 60s) to avoid repeated DB lookups
  - Clear role priority for legacy single‑role checks
  - Public paths configurable via env
  - No cookie‑based auth (CSRF‑safe by default)
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple, List

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from egregore.models.user import UserIdentity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (all overridable via environment)
# ---------------------------------------------------------------------------

PUBLIC_PATHS_ENV = os.environ.get("EGREGORE_PUBLIC_PATHS", "")
DEFAULT_PUBLIC_PATHS = {"/", "/health", "/health/ready", "/health/live", "/favicon.ico"}
if PUBLIC_PATHS_ENV:
    EXTRA_PUBLIC_PATHS = {p.strip() for p in PUBLIC_PATHS_ENV.split(",") if p.strip()}
    PUBLIC_PATHS = DEFAULT_PUBLIC_PATHS.union(EXTRA_PUBLIC_PATHS)
else:
    PUBLIC_PATHS = DEFAULT_PUBLIC_PATHS

CACHE_TTL_SECONDS = int(os.environ.get("EGREGORE_IDENTITY_CACHE_TTL", "60"))

ROLE_PRIORITY = ["admin", "operator", "user", "guest", "reader"]

# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

def _load_keys_from_env() -> Dict[str, Tuple[str, str, List[str]]]:
    """Load from EGREGORE_API_KEYS: comma‑separated key:tenant:user:role1|role2"""
    keys = {}
    env_val = os.environ.get("EGREGORE_API_KEYS", "")
    if not env_val:
        return keys

    for entry in env_val.split(","):
        parts = entry.strip().split(":")
        if len(parts) >= 1 and len(parts[0]) == 64:
            key = parts[0]
            tenant = parts[1] if len(parts) > 1 else "default"
            user = parts[2] if len(parts) > 2 else "api_user"
            roles = parts[3].split("|") if len(parts) > 3 else ["reader"]
            keys[key] = (tenant, user, roles)
        else:
            logger.warning(f"Ignoring invalid key entry (length != 64): {entry[:10]}...")
    return keys


def _load_keys_from_file(path: Path) -> Dict[str, Tuple[str, str, List[str]]]:
    """Load from JSON file: [{"key": "...", "tenant_id": "...", "user_id": "...", "roles": ["..."]}]"""
    if not path.exists():
        logger.warning(f"API key file not found: {path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        keys = {}
        for entry in data:
            key = entry.get("key", "")
            if not key or len(key) != 64:
                logger.warning(f"Ignoring invalid key in file: {key[:10]}...")
                continue
            tenant = entry.get("tenant_id", "default")
            user = entry.get("user_id", "api_user")
            roles = entry.get("roles", ["reader"])
            if isinstance(roles, str):
                roles = [roles]
            keys[key] = (tenant, user, roles)
        return keys
    except Exception as e:
        logger.error(f"Failed to load keys from {path}: {e}")
        return {}


def _load_keys_from_dotenv(repo_root: Path) -> Dict[str, Tuple[str, str, List[str]]]:
    """Fallback: parse EGREGORE_API_KEYS from .env in repo root."""
    dotenv_path = repo_root / ".env"
    if not dotenv_path.exists():
        return {}
    try:
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == "EGREGORE_API_KEYS":
                # Temporarily set env to reuse parser (no side effect left behind)
                old = os.environ.get("EGREGORE_API_KEYS")
                os.environ["EGREGORE_API_KEYS"] = v.strip().strip("\"'")
                keys = _load_keys_from_env()
                if old is not None:
                    os.environ["EGREGORE_API_KEYS"] = old
                else:
                    del os.environ["EGREGORE_API_KEYS"]
                return keys
    except Exception as e:
        logger.error(f"Failed to read .env: {e}")
    return {}


def _load_all_api_keys() -> Dict[str, Tuple[str, str, List[str]]]:
    """Load keys from env, file, .env (env overrides, file overrides .env)."""
    keys = _load_keys_from_env()
    if keys:
        return keys

    # File source
    file_path = os.environ.get("EGREGORE_API_KEYS_PATH", "")
    if file_path:
        file_keys = _load_keys_from_file(Path(file_path))
        keys.update(file_keys)
        if keys:
            return keys

    # .env source (repo root)
    repo_root = Path(__file__).resolve().parents[5]
    dotenv_keys = _load_keys_from_dotenv(repo_root)
    keys.update(dotenv_keys)
    return keys


_API_KEYS = _load_all_api_keys()
if not _API_KEYS:
    logger.warning("No API keys configured. All protected endpoints will return 401.")

# ---------------------------------------------------------------------------
# Identity resolution with cache
# ---------------------------------------------------------------------------

_identity_cache: Dict[str, Tuple[float, Optional[UserIdentity]]] = {}


def _resolve_identity(api_key: str) -> Optional[UserIdentity]:
    """Return identity for a key, using cache to avoid DB lookups."""
    now = time.time()
    cached = _identity_cache.get(api_key)
    if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    identity = None
    # 1. Try DB repository
    try:
        from egregore.infrastructure.persistence.user_repository import SQLiteUserRepository
        repo = SQLiteUserRepository()
        key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        identity = repo.find_by_api_key(key_hash)
    except Exception as e:
        logger.debug(f"DB lookup failed: {e}")

    # 2. Fallback to env keys
    if identity is None:
        env_identity = _API_KEYS.get(api_key)
        if env_identity:
            tenant_id, user_id, roles = env_identity
            identity = UserIdentity(
                tenant_id=tenant_id,
                user_id=user_id,
                username=user_id,
                email=None,
                roles=roles,
                vertical_grants=[],
                status="active",
                account_id=None,
            )

    # Cache result (even if None to avoid repeated DB misses for invalid keys)
    _identity_cache[api_key] = (now, identity)
    return identity


def _primary_role(roles: List[str]) -> str:
    """Return highest‑priority role for legacy single‑role checks."""
    for r in ROLE_PRIORITY:
        if r in roles:
            return r
    return roles[0] if roles else "reader"


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validate API key on every request and inject identity into request.state."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Public paths bypass authentication
        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        # Fail‑closed: no keys configured
        if not _API_KEYS:
            return JSONResponse(
                status_code=401,
                content={"detail": "API key authentication not configured"},
            )

        # Extract key from X-API-Key header or Authorization: Bearer
        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:].strip()

        if not api_key:
            return JSONResponse(status_code=401, content={"detail": "Missing API key"})

        identity = _resolve_identity(api_key)
        if identity is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid API key"})

        # Inject validated identity
        request.state.tenant_id = identity.tenant_id
        request.state.user_id = identity.user_id
        request.state.roles = identity.roles
        request.state.role = _primary_role(identity.roles)  # legacy single role
        request.state.user = identity
        request.state.authenticated = True

        return await call_next(request)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def require_auth(request: Request) -> Tuple[str, str, str]:
    """Dependency returning (tenant_id, user_id, primary_role)."""
    if not getattr(request.state, "authenticated", False):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return (
        request.state.tenant_id,
        request.state.user_id,
        request.state.role,
    )


def require_role(required_role: str):
    """Dependency factory enforcing a specific role."""
    def _check(request: Request) -> Tuple[str, str, str]:
        identity = require_auth(request)
        roles = getattr(request.state, "roles", [identity[2]])
        if required_role not in roles:
            raise HTTPException(status_code=403, detail=f"Role '{required_role}' required")
        return identity
    return _check


def get_user_identity(request: Request) -> UserIdentity:
    """Return full UserIdentity from request state."""
    identity = getattr(request.state, "user", None)
    if identity is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return identity

def is_valid_api_key(key: str) -> bool:
    """Return True if the supplied key is in the configured key registry."""
    return bool(key) and key in _API_KEYS

def get_identity_for_key(key: str) -> tuple[str, str, str] | None:
    """Return (tenant_id, user_id, role) for a valid key, or None."""
    return _API_KEYS.get(key)

def is_valid_api_key(key: str) -> bool:
    """Return True if the supplied key is in the configured key registry."""
    return bool(key) and key in _API_KEYS

def get_identity_for_key(key: str) -> tuple[str, str, str] | None:
    """Return (tenant_id, user_id, role) for a valid key, or None."""
    return _API_KEYS.get(key)
