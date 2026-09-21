"""ANCHORUM input validation helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Default maximum input size accepted by ANCHORUM engines (512 MiB).
MAX_INPUT_BYTES = 512 * 1024 * 1024

# Case IDs and operator names must be filesystem-safe, URL-safe identifiers.
_MAX_IDENTIFIER_LENGTH = 128
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_\-:.]+$")


def validate_input_size(
    source: Any,
    *,
    max_bytes: int = MAX_INPUT_BYTES,
    label: str = "input",
) -> None:
    """Raise ValueError if a path or bytes object exceeds the size limit."""
    size: int | None = None
    if isinstance(source, (str, Path)):
        size = Path(source).stat().st_size
    elif isinstance(source, bytes):
        size = len(source)

    if size is not None and size > max_bytes:
        raise ValueError(
            f"{label} size ({size} bytes) exceeds maximum allowed ({max_bytes} bytes)"
        )


def validate_case_id(case_id: str) -> None:
    """Raise ValueError if case_id is not a safe identifier."""
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("case_id must be a non-empty string")
    if len(case_id) > _MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"case_id exceeds {_MAX_IDENTIFIER_LENGTH} characters")
    if not _IDENTIFIER_RE.match(case_id):
        raise ValueError(
            "case_id contains invalid characters; allowed: A-Z, a-z, 0-9, _, -, :, ."
        )


def validate_operator(operator: str) -> None:
    """Raise ValueError if operator is not a safe identifier."""
    if not isinstance(operator, str) or not operator:
        raise ValueError("operator must be a non-empty string")
    if len(operator) > _MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"operator exceeds {_MAX_IDENTIFIER_LENGTH} characters")
    if not _IDENTIFIER_RE.match(operator):
        raise ValueError(
            "operator contains invalid characters; allowed: A-Z, a-z, 0-9, _, -, :, ."
        )
