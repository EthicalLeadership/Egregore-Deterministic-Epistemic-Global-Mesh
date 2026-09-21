"""Fail-closed freeze gate — every request is blocked if system is frozen.

Exemptions:
  - /dashboard/* (so operator can unfreeze)
  - /static/*
  - /login
  - /ready
  - /health
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("egregore.freeze_gate")

_EXEMPT_PATHS = {
    "/dashboard",
    "/dashboard/login",
    "/dashboard/logout",
    "/static",
    "/ready",
    "/health",
}


class FreezeGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Exempt: static assets, login, dashboard, health probes
        if any(path.startswith(p) for p in _EXEMPT_PATHS):
            return await call_next(request)

        # Fail-closed: check freeze state
        try:
            root = request.app.state.composition_root
            if root.freeze_controller.is_frozen:
                logger.warning(
                    "REQUEST_BLOCKED_BY_FREEZE",
                    extra={
                        "path": path,
                        "method": request.method,
                        "remote_addr": request.client.host if request.client else None,
                    },
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "System is frozen. Contact operator.",
                        "state": "FROZEN",
                    },
                )
        except AttributeError:
            # composition_root not ready — allow (startup only)
            pass

        return await call_next(request)
