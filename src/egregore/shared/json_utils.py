"""Shared JSON extraction utilities for factory and interface layers."""

from __future__ import annotations

import json
import re
from typing import Any

from egregore.shared.canonical import canonical_loads


def _extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from model output (fenced or raw)."""
    text = text.strip()
    parsed: dict[str, Any] | None = None

    # Try fenced JSON code block
    fence_match = re.search(
        r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE
    )
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            parsed = canonical_loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # Try raw JSON object / array
    raw_match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if raw_match:
        candidate = raw_match.group(1).strip()
        try:
            parsed = canonical_loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return None
