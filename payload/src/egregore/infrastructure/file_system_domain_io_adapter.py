"""Concrete filesystem adapter for ``egregore.interface.domain_io_port``."""

from __future__ import annotations

from pathlib import Path


class FileSystemDomainIoAdapter:
    """Plane-2 adapter that lets domain code read files without importing
    ``pathlib`` or ``open`` directly.
    """

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return Path(path).read_text(encoding=encoding)

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def list_files(self, directory: str, pattern: str) -> list[str]:
        return sorted(str(p) for p in Path(directory).glob(pattern))
