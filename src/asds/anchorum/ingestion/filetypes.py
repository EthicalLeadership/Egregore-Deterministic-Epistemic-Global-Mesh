"""Magic-byte container detection and MIME inference.

Stdlib only. Derived from anchorum.forensic.core.ingestion.
"""

from __future__ import annotations

from enum import Enum


class ContainerType(Enum):
    PDF = "pdf"
    OOXML = "ooxml"
    ODT = "odt"
    LEGACY_OFFICE = "legacy_office"
    EMAIL = "email"
    JPEG = "jpeg"
    PNG = "png"
    TIFF = "tiff"
    GIF = "gif"
    BMP = "bmp"
    ZIP = "zip"
    UNKNOWN = "unknown"


MAGIC_SIGNATURES: list[tuple[bytes, ContainerType, int]] = [
    (b"%PDF-", ContainerType.PDF, 0),
    (b"PK\x03\x04", ContainerType.OOXML, 0),
    (b"PK\x05\x06", ContainerType.ZIP, 0),
    (b"PK\x07\x08", ContainerType.ZIP, 0),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ContainerType.LEGACY_OFFICE, 0),
    (b"\xff\xd8\xff", ContainerType.JPEG, 0),
    (b"\x89PNG\r\n\x1a\n", ContainerType.PNG, 0),
    (b"II*\x00", ContainerType.TIFF, 0),
    (b"MM\x00*", ContainerType.TIFF, 0),
    (b"GIF87a", ContainerType.GIF, 0),
    (b"GIF89a", ContainerType.GIF, 0),
    (b"BM", ContainerType.BMP, 0),
    (b"From ", ContainerType.EMAIL, 0),
    (b"Return-Path:", ContainerType.EMAIL, 0),
    (b"Received:", ContainerType.EMAIL, 0),
    (b"MIME-Version:", ContainerType.EMAIL, 0),
]


def detect_container(  # noqa: C901
    data: bytes, filename_hint: str | None = None
) -> ContainerType:
    """Determine container type from magic bytes; fall back to heuristics."""
    if not data:
        return ContainerType.UNKNOWN

    for sig, ctype, offset in MAGIC_SIGNATURES:
        if data[offset : offset + len(sig)] == sig:
            if ctype in (ContainerType.OOXML, ContainerType.ZIP) and filename_hint:
                fname_lower = filename_hint.lower()
                if fname_lower.endswith(".odt"):
                    return ContainerType.ODT
                if fname_lower.endswith((".docx", ".pptx", ".xlsx")):
                    return ContainerType.OOXML
            return ctype

    sample = data[:256]
    if (
        sample.startswith(b"From:")
        or b"\nFrom:" in sample
        or b"\nTo:" in sample
        or b"\nSubject:" in sample
    ) and (b"\n\n" in sample or b"\r\n\r\n" in sample or sample.startswith(b"From:")):
        return ContainerType.EMAIL

    if b"/Type /Catalog" in data[:4096] or b"/Root" in data[:4096]:
        return ContainerType.PDF

    if b"PK" in data[:4]:
        if filename_hint and filename_hint.lower().endswith(".odt"):
            return ContainerType.ODT
        return ContainerType.ZIP

    return ContainerType.UNKNOWN


MIME_MAP: dict[ContainerType, str] = {
    ContainerType.PDF: "application/pdf",
    ContainerType.OOXML: "application/vnd.openxmlformats",
    ContainerType.ODT: "application/vnd.oasis.opendocument.text",
    ContainerType.LEGACY_OFFICE: "application/msword",
    ContainerType.EMAIL: "message/rfc822",
    ContainerType.JPEG: "image/jpeg",
    ContainerType.PNG: "image/png",
    ContainerType.TIFF: "image/tiff",
    ContainerType.GIF: "image/gif",
    ContainerType.BMP: "image/bmp",
    ContainerType.ZIP: "application/zip",
    ContainerType.UNKNOWN: "application/octet-stream",
}


def infer_mime_type(container: ContainerType, data: bytes) -> str | None:
    """Refine MIME type with content inspection."""
    base = MIME_MAP.get(container)
    if container == ContainerType.ODT and base:
        return "application/vnd.oasis.opendocument.text"
    if container == ContainerType.OOXML and base:
        if b"word/document.xml" in data[:4096]:
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if b"xl/workbook.xml" in data[:4096]:
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if b"ppt/presentation.xml" in data[:4096]:
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    return base
