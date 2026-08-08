"""Formal constitutional ports through which Plane-1 (domain) may receive data.

These ABCs are the Interface Synod's ratified contracts: they declare the exact
shape of the boundary between the pure domain and the physical world. Any
concrete adapter that speaks to files, object stores, networks, or encrypted
vaults must implement one of these ports; domain code must depend only on the
port, never on the adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ConstitutionDataSource(ABC):
    """Port for loading the Federation Constitution document.

    The constitution is a single, ratified document. Implementations are
    responsible for fetching the raw bytes from persistent storage; the domain
    is responsible for parsing and validating those bytes.
    """

    @abstractmethod
    def load(self) -> bytes:
        """Return the complete constitution document as raw bytes."""
        ...


class RuleRegistrySource(ABC):
    """Port for loading a rule registry (e.g. Quebec civil procedure rules).

    The registry is supplied as a single raw document (typically YAML). The
    domain parses the document into typed rule objects without knowledge of how
    it was retrieved.
    """

    @abstractmethod
    def load(self) -> bytes:
        """Return the complete rule registry document as raw bytes."""
        ...


class DossierDataSource(ABC):
    """Port for reading files inside a single dossier or evidence bundle.

    This is the dossier parser's only window on the filesystem. Implementations
    resolve the supplied paths against the dossier root; domain code remains
    ignorant of absolute paths, path separators, or storage backends.
    """

    @abstractmethod
    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """Return decoded text content of ``path`` relative to the dossier root."""
        ...

    @abstractmethod
    def read_bytes(self, path: str) -> bytes:
        """Return raw bytes of ``path`` relative to the dossier root."""
        ...

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Return True if ``path`` exists relative to the dossier root."""
        ...

    @abstractmethod
    def list_files(self, directory: str, pattern: str) -> list[str]:
        """Return absolute paths matching ``pattern`` under ``directory``."""
        ...
