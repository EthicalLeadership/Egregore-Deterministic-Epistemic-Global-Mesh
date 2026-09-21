from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from egregore.domain.semantics.ports import ISemanticsDomainAdapter  # noqa: F401
from egregore.domain.semantics_models import (
    AuditEvent,
    CommandAck,
    CommandResult,
    GenerateDossierCommand,
    OutboxEntry,
)


@dataclass(frozen=True)
class CommitResult:
    """Value object: result of an atomic T2 commit."""

    dossier_id: str
    version: int
    event_count: int
    trace_hash: str


@runtime_checkable
class IProvenanceSigner(Protocol):
    """Ed25519 or equivalent signer for .zarc canonical bytes."""

    def sign(self, canonical_bytes: bytes) -> str: ...


class IAuthzProvider(Protocol):
    def authorize_generate(self, *, command: GenerateDossierCommand) -> None:
        """
        Raise on failure.
        Implementation may raise SemanticsError or a generic exception.
        """


class ICaseStore(Protocol):
    def get_case_state(self, *, organization_id: str, case_id: str) -> str: ...

    def get_next_version_number(self, *, organization_id: str, case_id: str) -> int: ...


class IIdempotencyStore(Protocol):
    def get_success_result(self, *, input_fingerprint: str) -> CommandResult | None: ...

    def put_success_result(
        self, *, input_fingerprint: str, result: CommandResult
    ) -> None: ...


class ISnapshotStore(Protocol):
    def persist_snapshot(
        self,
        *,
        organization_id: str,
        case_id: str,
        version_number: int,
        version_id: str,
        data: Mapping,
    ) -> None: ...


class IEventLogStore(Protocol):
    def append_events(self, *, events: Iterable[AuditEvent]) -> None: ...


class IOutboxStore(Protocol):
    def append_outbox_entries(self, *, entries: Iterable[OutboxEntry]) -> None: ...


class IUsageCounterStore(Protocol):
    def apply_deltas(self, *, deltas: Iterable[tuple[str, str, int]]) -> None: ...


class ITransactionalPersistence(Protocol):
    """
    Unit-of-work / commit boundary for Priority-1 semantics.

    Must be atomic: either all T2 writes happen, or none.
    """

    def commit_generate_t2(
        self,
        *,
        command: GenerateDossierCommand,
        computed_data: Mapping,
        version_number: int,
        version_id: str,
        case_next_state: str,
        events: Iterable[AuditEvent],
        outbox_entries: Iterable[OutboxEntry],
        idempotency_fingerprint: str,
        usage_deltas: Iterable[tuple[str, str, int]],
        timestamp_ns: int,
    ) -> CommandAck: ...


class ISemanticsErrorFactory(Protocol):
    def validation(self, *, message: str) -> Exception: ...


# --- KimiK2 Loader Port (Plane 1/2 boundary) ---
class Kimik2LoaderError(Exception):
    """Deterministic error for Kimik2 loader failures."""

    pass


class IKimik2Loader(Protocol):
    @abstractmethod
    def generate(self, prompt: str, max_tokens: int, temperature: float = 0.0) -> str:
        """
        Deterministic inference. Temperature must be 0.0. Raises Kimik2LoaderError on any failure.
        """
        pass
