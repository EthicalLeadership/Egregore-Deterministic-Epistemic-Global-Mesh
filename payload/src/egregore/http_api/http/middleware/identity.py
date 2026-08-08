"""IdentityMiddleware - enforces mandatory identity headers on every request."""

from uuid import uuid4

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware


class IdentityMiddleware(BaseHTTPMiddleware):
    """
    Rejects any request missing:
      - X-Tenant-ID
      - X-User-ID
      - X-Role
      - X-Session-ID

    Attaches a generated trace_id and the full identity dict to
    request.state.identity for downstream handlers.
    """

    async def dispatch(self, request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-ID")
        user_id = request.headers.get("X-User-ID")
        role = request.headers.get("X-Role")
        session_id = request.headers.get("X-Session-ID")

        if not all([tenant_id, user_id, role, session_id]):
            raise HTTPException(
                status_code=400,
                detail="Missing identity headers: X-Tenant-ID, X-User-ID, X-Role, X-Session-ID",
            )

        request.state.identity = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "role": role,
            "session_id": session_id,
            "trace_id": str(uuid4()),
        }

        return await call_next(request)
