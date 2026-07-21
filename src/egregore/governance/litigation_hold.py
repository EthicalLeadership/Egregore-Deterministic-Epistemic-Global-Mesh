from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LitigationHoldRequest:
    case_id: str
    scope: list[str]
    reason: str


class LitigationHoldTrigger:
    """
    Thin wrapper around an injected ANCHORUM litigation-hold API callable.

    Injection keeps runtime dependencies optional; unit tests verify delegation + payload.
    """

    def __init__(self, *, anchorum_hold_api: Callable[..., Any]) -> None:
        self._anchorum_hold_api = anchorum_hold_api

    def trigger(self, *, case_id: str, scope: list[str], reason: str) -> str:
        # We keep the interface flexible by allowing anchorum callable signatures
        # to be passed via keyword arguments.
        result = self._anchorum_hold_api(case_id=case_id, scope=scope, reason=reason)
        # Contract: return a hold-id string.
        if not isinstance(result, str):
            raise TypeError(
                "litigation_hold: anchorum_hold_api must return a string hold id"
            )
        return result
