"""Bytes-based document metadata extraction for ANCHORUM.

This is a port-clean refactor of anchorum.forensic.core.document.metadata_extraction.
Instead of taking a Path, extract() takes bytes so ASDS owns content resolution.
"""

from __future__ import annotations

import io
import logging
import subprocess  # nosec B404
from dataclasses import dataclass, field
from typing import Any

try:
    import pikepdf
except ModuleNotFoundError:  # pragma: no cover
    pikepdf = None  # type: ignore[assignment]

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


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace") or None
    return str(value) or None


def _exiftool_present() -> bool:
    try:
        subprocess.run(
            ["exiftool", "-ver"],  # noqa: S607
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, Exception):
        return False


def extract_pdf_metadata(data: bytes) -> MetadataPlane:
    """Extract forensic metadata from a PDF byte stream."""
    if pikepdf is None:
        return MetadataPlane(raw={"error": "pikepdf not installed"})

    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            docinfo = pdf.docinfo
            producer = _decode(docinfo.get("/Producer")) if docinfo else None
            creator = _decode(docinfo.get("/Creator")) if docinfo else None
            created = _decode(docinfo.get("/CreationDate")) if docinfo else None
            modified = _decode(docinfo.get("/ModDate")) if docinfo else None
            title = _decode(docinfo.get("/Title")) if docinfo else None
            author = _decode(docinfo.get("/Author")) if docinfo else None

            raw: dict[str, Any] = {}
            if docinfo:
                raw = {str(k): _decode(v) for k, v in docinfo.items()}

            return MetadataPlane(
                producer=producer,
                creator=creator,
                created=created,
                modified=modified,
                title=title,
                author=author,
                encrypted=pdf.is_encrypted,
                exiftool_available=_exiftool_present(),
                raw=raw,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("metadata extraction failed: %s", exc)
        return MetadataPlane(raw={"error": f"parse error: {exc}"})


def extract_metadata(data: bytes, filename_hint: str | None = None) -> dict[str, Any]:
    """Extract metadata from raw evidence bytes.

    Currently supports PDF. Other containers return a basic size/container stub.
    """
    from modules.anchorum.ingestion.filetypes import ContainerType, detect_container

    container = detect_container(data, filename_hint=filename_hint)
    if container == ContainerType.PDF:
        result = extract_pdf_metadata(data).to_dict()
        result["container_type"] = container.value
        result["size_bytes"] = len(data)
        return result

    return {
        "container_type": container.value,
        "size_bytes": len(data),
        "note": "metadata extraction not yet implemented for this container",
    }
