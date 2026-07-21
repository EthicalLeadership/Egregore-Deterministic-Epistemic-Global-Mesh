"""Key health model for the dashboard — matched to template."""

from dataclasses import dataclass
from enum import StrEnum


class KeyHealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    SHORT = "SHORT"
    MISSING = "MISSING"
    EXPIRED = "EXPIRED"


@dataclass
class KeyHealth:
    has_key: bool
    key_length: int
    permissions: str
    min_required: int = 64
    last_rotated: float | None = None
    rotation_due: bool = False
    rotation_days_remaining: int | None = None

    @property
    def health(self) -> KeyHealthStatus:
        if not self.has_key:
            return KeyHealthStatus.MISSING
        if self.key_length < self.min_required:
            return KeyHealthStatus.SHORT
        return KeyHealthStatus.HEALTHY
