"""Per-route API key dependency.

Reads the key from the `X-API-Key` header OR the `api_key` cookie, in
that order. Matches the pattern used by `ws_chat.py` and
`anchorum_http.py` so the WebKit cookie works alongside curl callers.

CONTEXT — current middleware is a stub
    src/egregore/http_api/http/middleware/api_key_middleware.py is a
    documented compatibility stub. `is_valid_api_key` returns False for
    every input, and `ApiKeyMiddleware` is a no-op. Its docstring:
    "Local access is trusted."

    This dependency therefore does not consult `is_valid_api_key`.
    Policy:
      * Loopback callers (127.0.0.1 / ::1 / localhost) are trusted.
      * Non-loopback callers must present a non-empty key.
    When the real middleware ships, tighten this function (or delete it
    in favour of the global middleware).
"""

from __future__ import annotations

import logging

from fastapi import Header, HTTPException, Request

log = logging.getLogger("egregore.auth")

_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", ""})


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str:
    """Return the effective API key. Raises 401 only for remote callers
    that present no key at all."""
    client_host = (request.client.host if request.client else "") or ""
    cookie_key = request.cookies.get("api_key", "")
    key = (x_api_key or cookie_key or "").strip()

    if client_host in _LOCAL_HOSTS:
        log.info("auth.local path=%s", request.url.path)
        return key or "local"

    if not key:
        log.warning("auth.missing path=%s host=%s", request.url.path, client_host)
        raise HTTPException(401, "Missing API key")

    log.info("auth.remote path=%s host=%s", request.url.path, client_host)
    return key
