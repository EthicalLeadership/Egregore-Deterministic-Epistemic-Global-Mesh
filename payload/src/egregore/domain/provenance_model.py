from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProvenanceEvent:
    """
    Domain-level provenance event contract.

    This is infrastructure-agnostic: it does NOT include prev_hash/sig
    (those are storage/signing responsibilities).
    """

    engine: str
    event: str
    payload: Mapping[str, Any]
    ts_ns: int | None = None
