# epistemic marker: provenance / auditability
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _reject_non_finite_floats(obj: Any) -> Any:
    """
    Fail-closed: reject NaN/Inf so canonical serialization is safe and deterministic.
    """
    if isinstance(obj, float):
        if obj != obj:  # NaN
            raise ValueError("canonical JSON forbids NaN")
        # +/-Inf
        if obj in (float("inf"), float("-inf")):
            raise ValueError("canonical JSON forbids Infinity")
        return obj
    if isinstance(obj, dict):
        return {k: _reject_non_finite_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_reject_non_finite_floats(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_reject_non_finite_floats(v) for v in obj)
    return obj


def canonical_dumps(
    obj: Any, *, default=None, indent: int | None = None, sort_keys: bool = True
) -> str:
    """
    Canonical deterministic JSON string:
    - UTF-8 safe text (ensure_ascii=False)
    - Recursive key sorting via sort_keys=True
    - Stable separators (no whitespace when un-indented)
    - Fail-closed: NaN/Inf rejected

    ``sort_keys`` is accepted for backward compatibility with callers that
    previously passed it to ``json.dumps``; canonical output always sorts.
    """
    safe_obj = _reject_non_finite_floats(obj)
    kwargs = {
        "sort_keys": sort_keys,
        "separators": (",", ":") if indent is None else (",", ": "),
        "ensure_ascii": False,
        "allow_nan": False,
        "indent": indent,
    }
    try:
        return json.dumps(safe_obj, **kwargs)
    except TypeError:
        if default is not None:
            return json.dumps(safe_obj, default=default, **kwargs)
        raise


def canonical_json(data: Mapping[str, Any]) -> str:
    """
    Back-compat alias for canonical_dumps for existing code/tests.
    """
    return canonical_dumps(data)


def canonical_json_bytes(obj: Any) -> bytes:
    return canonical_dumps(obj).encode("utf-8")


def canonical_loads(s: str | bytes | bytearray) -> Any:
    """
    Canonical decoder companion. (Decoding is whitespace-insensitive; hashes/signatures
    are produced by canonical_dumps only.)
    """
    if isinstance(s, (bytes, bytearray)):
        s = bytes(s).decode("utf-8")
    return json.loads(s)


def canonical_load_file(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return canonical_loads(f.read())


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
