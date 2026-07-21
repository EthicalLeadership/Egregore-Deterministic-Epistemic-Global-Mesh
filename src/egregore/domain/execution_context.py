"""ExecutionContext - immutable identity and causality context for every execution."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExecutionContext:
    """
    Mandatory context for every ExecutionGuard invocation.

    All fields are immutable. The context travels with the execution
    through every layer and is logged at completion.
    """

    tenant_id: str
    user_id: str
    role: str
    session_id: str
    trace_id: str
    subsystem: str
    operation: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def principal_id(self) -> str:
        """SEL-X principal identifier: aliases user_id for authorization."""
        return self.user_id
