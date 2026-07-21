"""In-memory feature flag registry for guard policy checks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass
class FeatureFlag:
    name: str
    enabled: bool = False
    allowed_tenants: Sequence[str] = field(default_factory=tuple)
    allowed_roles: Sequence[str] = field(default_factory=tuple)


class FeatureFlagRegistry:
    """Thread-safe in-memory feature flag registry.

    Production deployments can replace this with a persistence-backed
    implementation via the same port interface.
    """

    def __init__(self, flags: dict[str, FeatureFlag] | None = None) -> None:
        self._flags: dict[str, FeatureFlag] = dict(flags) if flags else {}

    def register(self, flag: FeatureFlag) -> None:
        self._flags[flag.name] = flag

    def is_enabled(self, name: str, *, tenant_id: str = "", role: str = "") -> bool:
        flag = self._flags.get(name)
        if flag is None:
            return False
        if not flag.enabled:
            return False
        if flag.allowed_tenants and tenant_id not in flag.allowed_tenants:
            return False
        return not (flag.allowed_roles and role not in flag.allowed_roles)

    def get(self, name: str) -> FeatureFlag | None:
        return self._flags.get(name)

    def all_flags(self) -> dict[str, FeatureFlag]:
        return dict(self._flags)
