"""Protocol for freeze control — matches FreezeController API."""

from typing import Any, Protocol


class FreezeControllerProtocol(Protocol):
    state: Any  # FreezeState enum
    is_frozen: bool  # property
    tenant_id: str
    history: list[dict[str, Any]]

    def freeze(self, reason: str, operator_id: str, **kwargs) -> None: ...
    def unfreeze(self, reason: str, operator_id: str) -> None: ...
    def reset(self, reason: str, operator_id: str) -> None: ...
