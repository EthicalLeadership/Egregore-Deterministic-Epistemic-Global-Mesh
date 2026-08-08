"""ANCHORUM capability manifest registry (stub).

Tracks tools registered by CUSTOM-xxx modules. In a full deployment this
would persist to a governed capability manifest file.
"""

from __future__ import annotations

from typing import Any

TOOLS: list[dict[str, Any]] = []


def register_tool(**kwargs: Any) -> None:
    """Register a forensic tool in the capability manifest."""
    TOOLS.append(kwargs)


def registered_tools() -> list[dict[str, Any]]:
    """Return a snapshot of registered tools."""
    return TOOLS.copy()
