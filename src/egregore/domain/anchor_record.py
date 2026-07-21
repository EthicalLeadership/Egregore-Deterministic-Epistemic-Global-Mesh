"""Domain model for public anchor records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class AnchorRecord:
    """Public notarization receipt for an execution block.

    - ``anchor_id`` is deterministic from block_hash + tier.
    - ``tier`` describes anchoring strength (e.g. "tsa", "blockchain", "manual").
    - ``notarization`` is the raw receipt from the timestamp authority.
    - ``public_verify`` is true if the receipt can be independently verified.
    """

    anchor_id: str
    tier: str
    block_hash: str
    notarization: str
    public_verify: bool
    timestamp_ns: int
    metadata: Mapping[str, str]
