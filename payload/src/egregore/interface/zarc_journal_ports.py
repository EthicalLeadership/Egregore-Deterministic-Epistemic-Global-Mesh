from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from egregore.domain.semantics_models import AuditEvent, OutboxEntry


class ICommitJournal(Protocol):
    """
    Read interface for an append-only provenance journal.

    The journal is expected to provide deterministic, replay-verifiable
    persistence for:
    - snapshot payload (computed_data)
    - audit events
    - outbox entries

    Implementations should return None if the execution_id is unknown.
    """

    def get_committed_snapshot(
        self,
        *,
        execution_id: str,
    ) -> Mapping[str, Any] | None: ...

    def get_committed_events(
        self,
        *,
        execution_id: str,
    ) -> Sequence[AuditEvent] | None: ...

    def get_committed_outbox_entries(
        self,
        *,
        execution_id: str,
    ) -> Sequence[OutboxEntry] | None: ...
