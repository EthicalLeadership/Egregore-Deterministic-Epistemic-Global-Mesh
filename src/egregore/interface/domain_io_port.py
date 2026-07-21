"""Ports through which domain code may perform external I/O.

Domain (Plane 1) must remain pure: it defines these protocols but never
implements them with concrete filesystem/network calls. Adapters in
``egregore.infrastructure`` provide the concrete implementations and are
injected at bootstrap time.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DomainIoPort(Protocol):
    """Abstract surface for file-system-like reads used by domain code."""

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """Return the decoded text content of ``path``."""
        ...

    def read_bytes(self, path: str) -> bytes:
        """Return the raw bytes of ``path``."""
        ...

    def exists(self, path: str) -> bool:
        """Return True if ``path`` exists."""
        ...

    def list_files(self, directory: str, pattern: str) -> list[str]:
        """Return absolute paths matching ``pattern`` under ``directory``."""
        ...
