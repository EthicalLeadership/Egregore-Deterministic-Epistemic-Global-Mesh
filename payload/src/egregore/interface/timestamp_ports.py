"""Ports for timestamp authority clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TimestampError(Exception):
    pass


@dataclass(frozen=True)
class TimestampResponse:
    token: str
    timestamp_ns: int
    verified: bool
    source: str  # "tsa" or "local"


class ITimestampClient(Protocol):
    def timestamp(self, data_hash: str) -> TimestampResponse: ...
