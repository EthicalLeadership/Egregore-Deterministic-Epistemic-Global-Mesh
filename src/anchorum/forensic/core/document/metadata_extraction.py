"""ANCHORUM document metadata extraction (Plane 2).

Pulls document metadata from PDF Info dictionaries and XMP packets using
``pikepdf``. ExifTool is left as an optional external subprocess when present;
core logic does not link against it.
"""

from __future__ import annotations

import logging
import subprocess  # nosec B404
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pikepdf

from anchorum.forensic.core.shell import _run_external

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetadataPlane:
    producer: str | None = None
    creator: str | None = None
    created: str | None = None
    modified: str | None = None
    title: str | None = None
    author: str | None = None
    encrypted: bool = False
    exiftool_available: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "producer": self.producer,
            "creator": self.creator,
            "created": self.created,
            "modified": self.modified,
            "title": self.title,
            "author": self.author,
            "encrypted": self.encrypted,
            "exiftool_available": self.exiftool_available,
            "raw": self.raw,
        }


class MetadataExtractor:
    """Extract forensic metadata from PDFs."""

    def extract(self, path: Path | str) -> MetadataPlane:
        pdf_path = Path(path)
        if not pdf_path.exists():
            return MetadataPlane(raw={"error": "file not found"})

        try:
            with pikepdf.open(pdf_path) as pdf:
                docinfo = pdf.docinfo
                producer = self._decode(docinfo.get("/Producer")) if docinfo else None
                creator = self._decode(docinfo.get("/Creator")) if docinfo else None
                created = (
                    self._decode(docinfo.get("/CreationDate")) if docinfo else None
                )
                modified = self._decode(docinfo.get("/ModDate")) if docinfo else None
                title = self._decode(docinfo.get("/Title")) if docinfo else None
                author = self._decode(docinfo.get("/Author")) if docinfo else None

                raw: dict[str, Any] = {}
                if docinfo:
                    raw = {str(k): self._decode(v) for k, v in docinfo.items()}

                return MetadataPlane(
                    producer=producer,
                    creator=creator,
                    created=created,
                    modified=modified,
                    title=title,
                    author=author,
                    encrypted=pdf.is_encrypted,
                    exiftool_available=self._exiftool_present(),
                    raw=raw,
                )
        except pikepdf.PdfError as exc:
            logger.warning("metadata extraction failed for %s: %s", pdf_path, exc)
            return MetadataPlane(raw={"error": f"parse error: {exc}"})

    @staticmethod
    def _decode(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace") or None
        return str(value) or None

    @staticmethod
    def _exiftool_present() -> bool:
        try:
            _run_external(
                ["exiftool", "-ver"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=5,
            )
            return True
        except FileNotFoundError:
            return False
        except Exception as exc:  # noqa: BLE001
            logger.debug("exiftool presence check failed: %s", exc)
            return False
