"""API key middleware. Reads EGREGORE_API_KEYS at import time.

Format: key1:user1:role1:scope1,key2:user2:role2:scope2
Accepts X-API-Key header or api_key cookie.
Exempts /health, /docs, /redoc, /openapi.json, /static, /dashboard, /favicon.ico.

Requires load_dotenv() before this module is imported. Guarded by Step D.

Public surface kept compatible with the previous stub:
  ApiKeyMiddleware, APIKeyMiddleware, is_valid_api_key,
  get_identity_for_key, get_user_identity, get_identity, _hash_key,
  _API_KEYS (list of raw keys), _resolve_identity.

Note: BaseHTTPMiddleware covers HTTP only. WebSocket handshakes bypass
it; ws_chat.py authenticates WS via the api_key cookie or query param.
"""

from __future__ import annotations

import hashlib
import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger("egregore.middleware")

_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/health", "/docs", "/redoc", "/openapi.json",
    "/static", "/dashboard", "/favicon.ico",
)


def _parse_keys(raw: str) -> dict[str, dict[str, str]]:
    parsed: dict[str, dict[str, str]] = {}
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        key = parts[0].strip()
        if not key:
            continue
        parsed[key] = {
            "user":  parts[1] if len(parts) > 1 else "",
            "role":  parts[2] if len(parts) > 2 else "",
            "scope": parts[3] if len(parts) > 3 else "",
        }
    return parsed


_KEY_MAP: dict[str, dict[str, str]] = _parse_keys(
    os.environ.get("EGREGORE_API_KEYS", "")
)

# List shape preserved for backward compatibility (ws_chat.py passes this
# list to repo.bootstrap_admin_if_needed).
_API_KEYS: list[str] = list(_KEY_MAP.keys())

if not _API_KEYS:
    log.warning("EGREGORE_API_KEYS empty at import; non-exempt requests will 401")
else:
    log.info("loaded %d API keys from env", len(_API_KEYS))


def is_valid_api_key(api_key: str) -> bool:
    return api_key in _KEY_MAP


def get_identity_for_key(api_key: str):
    return _KEY_MAP.get(api_key)


def get_user_identity(api_key: str = ""):
    info = _KEY_MAP.get(api_key)
    return info["user"] if info else None


def get_identity(api_key: str = ""):
    return _KEY_MAP.get(api_key)


def _resolve_identity(api_key: str):
    """Return an identity token for a valid key, or None.

    Matches the pattern used by ws_chat.py:
      caller_identity_token = f"api_key:{api_key}"
    """
    if api_key in _KEY_MAP:
        return f"api_key:{api_key}"
    return None


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def _is_exempt(path: str) -> bool:
    for prefix in _EXEMPT_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Reject non-exempt requests without a valid API key.

    WebSocket handshakes are not covered by BaseHTTPMiddleware.
    ws_chat.py authenticates WS independently.
    """

    async def dispatch(self, request, call_next):
        path = request.url.path
        if _is_exempt(path):
            return await call_next(request)

        header_key = (request.headers.get("X-API-Key") or "").strip()
        cookie_key = (request.cookies.get("api_key") or "").strip()
        key = header_key or cookie_key

        if not key:
            log.warning("auth.missing path=%s", path)
            return JSONResponse({"detail": "Missing API key"}, status_code=401)
        if key not in _KEY_MAP:
            log.warning("auth.invalid path=%s key=%s...", path, key[:8])
            return JSONResponse({"detail": "Invalid API key"}, status_code=401)

        request.state.api_key = key
        request.state.api_identity = _KEY_MAP[key]
        return await call_next(request)


APIKeyMiddleware = ApiKeyMiddleware

__all__ = [
    "ApiKeyMiddleware", "APIKeyMiddleware", "is_valid_api_key",
    "get_identity_for_key", "get_user_identity", "get_identity",
    "_hash_key", "_API_KEYS", "_resolve_identity",
]
