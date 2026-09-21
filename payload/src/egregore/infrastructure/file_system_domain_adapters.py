"""Filesystem adapters for the formal domain-data ports.

These are Plane-2 implementations of the constitutional ports declared in
``egregore.interface.domain_data_ports``. They are the physical hands that
fetch bytes; they hold no interpretive authority.

Security notes:
- ``FileSystemDossierAdapter`` rejects absolute paths and ``..`` traversal in
  both direct file access and ``list_files`` patterns.
- The adapter resolves paths at call time, so symlinks that point outside the
  dossier root are caught.
- Reads are performed with ``O_NOFOLLOW`` after resolution, which closes the
  most common time-of-check/time-of-use race where a regular file is replaced
  by an outside-pointing symlink between resolution and read.
- It does **not** defend against hard links (which share an inode and never
  leave the dossier path) or post-open file replacement. Those would require
  inode-level checks and are out of scope here.
"""

from __future__ import annotations

import os
from pathlib import Path

from egregore.interface.domain_data_ports import (
    ConstitutionDataSource,
    DossierDataSource,
    RuleRegistrySource,
)


class FileSystemConstitutionAdapter(ConstitutionDataSource):
    """Load the Federation Constitution from a file on disk."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def load(self) -> bytes:
        if not self._path.is_file():
            raise FileNotFoundError(f"Constitution not found: {self._path}")
        return self._path.read_bytes()


class FileSystemRuleRegistryAdapter(RuleRegistrySource):
    """Load a rule registry from a single YAML file on disk."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def load(self) -> bytes:
        if not self._path.is_file():
            raise FileNotFoundError(f"Rule registry not found: {self._path}")
        return self._path.read_bytes()


class FileSystemDossierAdapter(DossierDataSource):
    """Read files inside a dossier directory on disk.

    The adapter enforces that every accessed path stays within the dossier
    root. Absolute paths, ``..`` traversal, and symlink escapes are rejected.
    """

    def __init__(self, root: Path | str) -> None:
        # Resolve once at construction so a stable, real root boundary is used
        # for every subsequent check. This also defends against symlink races
        # in the root's ancestry after the adapter is created.
        self._root = Path(root).resolve(strict=False)

    def _resolve(self, path: str) -> Path:
        raw = Path(path)
        if raw.is_absolute():
            raise ValueError("Absolute paths are not allowed inside a dossier")
        full = (self._root / raw).resolve(strict=False)
        if not full.is_relative_to(self._root):
            raise ValueError(f"Path escapes dossier root: {path}")
        return full

    @staticmethod
    def _read_bytes_nofollow(path: Path) -> bytes:
        """Read file bytes without following symlinks (TOCTOU mitigation)."""
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            # Read in chunks until EOF. This avoids depending on st_size,
            # which is 0 for FIFOs and may race with file changes.
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 8192)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(fd)

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        resolved = self._resolve(path)
        data = self._read_bytes_nofollow(resolved)
        return data.decode(encoding)

    def read_bytes(self, path: str) -> bytes:
        return self._read_bytes_nofollow(self._resolve(path))

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def list_files(self, directory: str, pattern: str) -> list[str]:
        base = self._resolve(directory)
        if not base.is_dir():
            return []

        pattern_path = Path(pattern)
        if (
            pattern_path.is_absolute()
            or ".." in pattern_path.parts
            or "**" in pattern_path.parts
        ):
            raise ValueError(
                "Pattern must not contain absolute paths, '..' or '**' components"
            )

        # Defence in depth: ensure the glob pattern itself cannot resolve outside
        # the dossier root even on platforms with exotic path separators.
        dummy = (base / pattern_path).resolve(strict=False)
        if not dummy.is_relative_to(base):
            raise ValueError(f"Pattern escapes dossier root: {pattern}")

        return sorted(str(p) for p in base.glob(pattern))
