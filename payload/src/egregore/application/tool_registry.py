"""Runtime tool registry for SEL-X.

Provides schema-hardened tool discovery and invocation. Tools declare their
input schema, output schema, and allowed domains; the registry enforces these
contracts at invocation time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSchema:
    """Contract for a registered tool."""

    name: str
    input_schema: Mapping[str, str]
    output_schema: Mapping[str, str]
    allowed_domains: Sequence[str] = field(default_factory=tuple)
    deterministic: bool = True


class ToolInvocationError(Exception):
    pass


class ToolRegistry:
    """In-memory registry for deterministic, schema-hardened tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSchema] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register(
        self,
        schema: ToolSchema,
        handler: Callable[..., Any],
    ) -> None:
        self._tools[schema.name] = schema
        self._handlers[schema.name] = handler

    def list_tools(self) -> Sequence[str]:
        return tuple(self._tools.keys())

    def get_schema(self, name: str) -> ToolSchema | None:
        return self._tools.get(name)

    def invoke(
        self, name: str, inputs: Mapping[str, Any], *, domain: str = "default"
    ) -> Any:
        schema = self._tools.get(name)
        if schema is None:
            raise ToolInvocationError(f"Tool not registered: {name}")
        if schema.allowed_domains and domain not in schema.allowed_domains:
            raise ToolInvocationError(
                f"Domain '{domain}' not allowed for tool '{name}'"
            )

        # Validate required inputs are present.
        missing = [k for k in schema.input_schema if k not in inputs]
        if missing:
            raise ToolInvocationError(f"Missing inputs for '{name}': {missing}")

        handler = self._handlers[name]
        result = handler(**inputs)

        # Basic output shape validation for mappings.
        if isinstance(result, dict) and schema.output_schema:
            missing_outputs = [k for k in schema.output_schema if k not in result]
            if missing_outputs:
                raise ToolInvocationError(
                    f"Missing outputs from '{name}': {missing_outputs}"
                )

        return result
