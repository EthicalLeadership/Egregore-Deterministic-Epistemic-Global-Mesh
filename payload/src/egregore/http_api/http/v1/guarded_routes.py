"""Guarded route handlers — IdentityMiddleware + ExecutionGuard wiring."""

from fastapi import HTTPException, Request

from egregore.domain.execution_context import ExecutionContext


def build_context(request: Request, subsystem: str, operation: str) -> ExecutionContext:
    """Extract identity from request.state and build ExecutionContext."""
    identity = getattr(request.state, "identity", None)
    if identity is None:
        raise HTTPException(
            status_code=400,
            detail="Identity not found — is IdentityMiddleware registered?",
        )
    return ExecutionContext(
        tenant_id=identity["tenant_id"],
        user_id=identity["user_id"],
        role=identity["role"],
        session_id=identity["session_id"],
        trace_id=identity["trace_id"],
        subsystem=subsystem,
        operation=operation,
    )
