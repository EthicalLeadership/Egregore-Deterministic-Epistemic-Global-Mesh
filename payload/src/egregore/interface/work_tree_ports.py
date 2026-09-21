"""Ports for WorkTree decomposition and persistence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from egregore.domain.work_tree import WorkTree
from egregore.domain.work_unit import WorkUnit


@dataclass(frozen=True)
class WorkSpec:
    """Recursive decomposition spec: a work unit plus its child specs."""

    work_unit: WorkUnit
    children: tuple["WorkSpec", ...] = field(default_factory=tuple)


class IWorkDecomposition(Protocol):
    """Maps a work unit to its decomposition tree.

    Implementations MUST be deterministic: the same work unit always
    decomposes into the same WorkSpec tree (stable IDs, no wall-clock,
    no randomness).
    """

    def decompose(self, work_unit: WorkUnit) -> WorkSpec: ...


class IWorkTreeStore(Protocol):
    """Persistence port for work trees."""

    def save(self, tree: WorkTree) -> None: ...
    def load(self, tree_id: str) -> WorkTree | None: ...
    def list_tree_ids(self) -> Sequence[str]: ...
