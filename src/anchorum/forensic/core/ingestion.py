"""
ANCHORUM Forensic Ingestion Layer
==================================
Artifact ingestion, magic-byte detection, hashing, and filesystem metadata.
Stdlib only. Python 3.11+. CBI-0 governed.

CBI-0:
- M1: Read-only enforcement. Original file must be readable, NOT writable.
- M2: Tool registration.
- M3: Terminal immutable output (Artifact dataclass).
- M4: .zarc event emission on every ingest.
"""

from __future__ import annotations

import contextlib
import grp
import hashlib
import logging
import os
import pwd
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from anchorum.forensic.core.types import (
    Artifact,
    ContainerType,
    FsMetadata,
    ZarcEventType,
    emit_zarc_event,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Magic Byte Detection (stdlib only, no libmagic)
# ---------------------------------------------------------------------------
MAGIC_SIGNATURES: list[tuple[bytes, ContainerType, int]] = [
    # PDF
    (b"%PDF-", ContainerType.PDF, 0),
    # OOXML — ZIP-based
    (b"PK\x03\x04", ContainerType.OOXML, 0),
    (b"PK\x05\x06", ContainerType.ZIP, 0),  # empty ZIP
    (b"PK\x07\x08", ContainerType.ZIP, 0),  # spanned ZIP
    # Legacy Office (OLE2)
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ContainerType.LEGACY_OFFICE, 0),
    # Images
    (b"\xff\xd8\xff", ContainerType.JPEG, 0),
    (b"\x89PNG\r\n\x1a\n", ContainerType.PNG, 0),
    (b"II*\x00", ContainerType.TIFF, 0),  # little-endian TIFF
    (b"MM\x00*", ContainerType.TIFF, 0),  # big-endian TIFF
    (b"GIF87a", ContainerType.GIF, 0),
    (b"GIF89a", ContainerType.GIF, 0),
    (b"BM", ContainerType.BMP, 0),
    # Email
    (b"From ", ContainerType.EMAIL, 0),
    (b"Return-Path:", ContainerType.EMAIL, 0),
    (b"Received:", ContainerType.EMAIL, 0),
    (b"MIME-Version:", ContainerType.EMAIL, 0),
]


def detect_container(  # noqa: C901
    data: bytes, filename_hint: str | None = None
) -> ContainerType:
    """
    Determine container type from magic bytes.
    Falls back to heuristics if no magic match.
    Uses filename_hint to disambiguate ZIP-based formats (OOXML vs ODT).
    """
    if not data:
        return ContainerType.UNKNOWN

    for sig, ctype, offset in MAGIC_SIGNATURES:
        if data[offset : offset + len(sig)] == sig:
            # Disambiguate ZIP-based formats by filename extension
            if ctype in (ContainerType.OOXML, ContainerType.ZIP) and filename_hint:
                fname_lower = filename_hint.lower()
                if fname_lower.endswith(".odt"):
                    return ContainerType.ODT
                if fname_lower.endswith((".docx", ".pptx", ".xlsx")):
                    return ContainerType.OOXML
            return ctype

    # Heuristic: if it looks like ASCII text with email headers
    sample = data[:256]
    if (
        sample.startswith(b"From:")
        or b"\nFrom:" in sample
        or b"\nTo:" in sample
        or b"\nSubject:" in sample
    ) and (b"\n\n" in sample or b"\r\n\r\n" in sample or sample.startswith(b"From:")):
        return ContainerType.EMAIL

    # Heuristic: if it contains PDF objects but missing header (corrupted/repaired)
    if b"/Type /Catalog" in data[:4096] or b"/Root" in data[:4096]:
        return ContainerType.PDF

    # Heuristic: ZIP without proper header (truncated)
    if b"PK" in data[:4]:
        if filename_hint and filename_hint.lower().endswith(".odt"):
            return ContainerType.ODT
        return ContainerType.ZIP

    return ContainerType.UNKNOWN


# ---------------------------------------------------------------------------
# 2. MIME Type Inference (from magic, not extension)
# ---------------------------------------------------------------------------
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
        # Peek inside ZIP to determine exact OOXML type
        if b"word/document.xml" in data[:4096]:
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if b"xl/workbook.xml" in data[:4096]:
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if b"ppt/presentation.xml" in data[:4096]:
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    return base


# ---------------------------------------------------------------------------
# 3. Filesystem Metadata Extraction
# ---------------------------------------------------------------------------
def extract_fs_metadata(path: Path) -> FsMetadata:
    """
    Extract all available filesystem metadata from a path.
    Cross-platform: Linux, macOS, Windows (where applicable).
    """
    st = os.stat(path, follow_symlinks=False)

    birth_time: datetime | None = None
    # st_birthtime exists on macOS/BSD; on Linux we try statx or debugfs
    if hasattr(st, "st_birthtime"):
        birth_time = datetime.fromtimestamp(st.st_birthtime, tz=UTC)

    mod_time = datetime.fromtimestamp(st.st_mtime, tz=UTC)
    access_time = datetime.fromtimestamp(st.st_atime, tz=UTC)

    owner_name: str | None = None
    group_name: str | None = None
    with contextlib.suppress(KeyError, ImportError):
        owner_name = pwd.getpwuid(st.st_uid).pw_name
    with contextlib.suppress(KeyError, ImportError):
        group_name = grp.getgrgid(st.st_gid).gr_name

    # Extended attributes (Linux/macOS)
    xattrs: dict[str, bytes] = {}
    if hasattr(os, "listxattr"):
        try:
            for attr_name in os.listxattr(path):
                xattrs[attr_name] = os.getxattr(path, attr_name)
        except OSError:
            pass

    # Alternate Data Streams (Windows NTFS) — detect via colon in filenames or Win32 API
    ads: list[str] = []
    if os.name == "nt":
        # Windows-specific: list streams with Win32 API
        pass  # Placeholder; would require ctypes

    # Filesystem type detection
    fs_type: str | None = None
    with contextlib.suppress(Exception):
        # Linux: read /proc/mounts or use statfs
        os.statvfs(path)
        # We can infer some filesystems from f_fsid or f_frsize, but it is unreliable
        # Best effort: check /proc/mounts for the mount point
        mount_point = _find_mount_point(path)
        fs_type = _get_fs_type_from_mounts(mount_point)

    return FsMetadata(
        birth_time=birth_time,
        mod_time=mod_time,
        access_time=access_time,
        inode=st.st_ino,
        device=st.st_dev,
        mode=st.st_mode,
        owner_uid=st.st_uid,
        owner_name=owner_name,
        group_gid=st.st_gid,
        group_name=group_name,
        hardlink_count=st.st_nlink,
        extended_attrs=xattrs,
        alternate_data_streams=tuple(ads),
        filesystem_type=fs_type,
    )


def _find_mount_point(path: Path) -> Path:
    """Find the mount point for a given path."""
    p = path.resolve()
    while not p.is_mount() and p != p.parent:
        p = p.parent
    return p


def _get_fs_type_from_mounts(mount_point: Path) -> str | None:
    """Read /proc/mounts to determine filesystem type. Linux only."""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == str(mount_point):
                    return parts[2]
    except (OSError, PermissionError):
        pass
    return None


# ---------------------------------------------------------------------------
# 4. Hashing
# ---------------------------------------------------------------------------
def hash_bytes(data: bytes, algorithm: str = "sha256") -> str:
    """Return hex digest of data using specified algorithm."""
    h = hashlib.new(algorithm)
    h.update(data)
    return h.hexdigest()


def hash_file(path: Path, algorithm: str = "sha256") -> str:
    """Stream-hash a file. Memory-efficient for large files."""
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_stream(stream: BinaryIO, algorithm: str = "sha256") -> str:
    """Hash from a readable stream without closing it."""
    h = hashlib.new(algorithm)
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 5. Ingestion API
# ---------------------------------------------------------------------------
class IngestionError(Exception):
    """Raised when artifact ingestion fails CBI-0 checks."""


class IngestionGuard:
    """
    CBI-0 M1 enforcement: original file must be readable, NOT writable.
    No writes to the original path. Ever.
    """

    @staticmethod
    def enforce(path: Path) -> None:
        if not path.exists():
            raise IngestionError(f"Artifact not found: {path}")
        if not path.is_file():
            raise IngestionError(f"Not a file: {path}")
        if not os.access(path, os.R_OK):
            raise IngestionError(f"Not readable: {path}")
        if os.access(path, os.W_OK):
            raise IngestionError(
                f"Original file must be read-only (writable detected): {path}"
            )
        if os.access(path, os.X_OK):
            # Executable documents are suspicious but not blocked
            logger.warning("Executable bit set on document: %s", path)


def ingest_artifact(
    source_path: Path | str,
    case_id: str,
    operator: str,
    enforce_readonly: bool = True,
) -> Artifact:
    """
    Ingest a single artifact into ANCHORUM.

    Args:
        source_path: Absolute or relative path to the file.
        case_id: Case identifier for .zarc event.
        operator: Operator username for .zarc event.
        enforce_readonly: If True, fail if file is writable (M1).

    Returns:
        Immutable Artifact with SHA-256, filesystem metadata, and container type.

    """
    path = Path(source_path).resolve()

    # M1: Read-only enforcement
    if enforce_readonly:
        IngestionGuard.enforce(path)

    # Hash
    artifact_id = hash_file(path)

    # Read first 8KB for magic detection and MIME refinement
    with open(path, "rb") as f:
        header = f.read(8192)

    container = detect_container(header, filename_hint=path.name)
    mime = infer_mime_type(container, header)

    # Filesystem metadata
    fs_meta = extract_fs_metadata(path)

    artifact = Artifact(
        artifact_id=artifact_id,
        source_path=str(path),
        ingest_time=datetime.now(UTC),
        size_bytes=path.stat().st_size,
        container_type=container,
        mime_type=mime,
        original_filename=path.name,
        filesystem_metadata=fs_meta,
    )

    logger.info(
        "Ingested artifact %s (%s, %d bytes) from %s",
        artifact_id[:16],
        container.value,
        artifact.size_bytes,
        path,
    )

    # M4: Emit .zarc event (stubbed in core types for stdlib-only operation)
    emit_zarc_event(
        event_type=ZarcEventType.ARTIFACT_INGESTED,
        case_id=case_id,
        operator=operator,
        payload={
            "artifact_id": artifact_id,
            "container_type": container.value,
            "mime_type": mime,
            "size_bytes": artifact.size_bytes,
            "source_path": str(path),
            "birth_time": (
                fs_meta.birth_time.isoformat() if fs_meta.birth_time else None
            ),
            "mod_time": fs_meta.mod_time.isoformat(),
            "owner": fs_meta.owner_name or fs_meta.owner_uid,
            "group": fs_meta.group_name or fs_meta.group_gid,
        },
    )

    return artifact


def ingest_directory(
    directory: Path | str,
    case_id: str,
    operator: str,
    recursive: bool = True,
    enforce_readonly: bool = True,
) -> tuple[Artifact, ...]:
    """
    Ingest all files in a directory.

    Returns:
        Tuple of immutable Artifacts.

    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise IngestionError(f"Not a directory: {directory}")

    artifacts: list[Artifact] = []
    pattern = "**/*" if recursive else "*"

    for path in directory.glob(pattern):
        if path.is_file() and not path.is_symlink():
            try:
                artifact = ingest_artifact(
                    path,
                    case_id=case_id,
                    operator=operator,
                    enforce_readonly=enforce_readonly,
                )
                artifacts.append(artifact)
            except IngestionError as exc:
                logger.warning("Skipping %s: %s", path, exc)

    logger.info(
        "Directory ingestion complete: %d artifacts from %s", len(artifacts), directory
    )
    return tuple(artifacts)


# ---------------------------------------------------------------------------
# 6. Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import tempfile

    # Test 1: Magic detection
    if not (detect_container(b"%PDF-1.4\n") == ContainerType.PDF):
        raise AssertionError
    if not (detect_container(b"PK\x03\x04") == ContainerType.OOXML):
        raise AssertionError
    if not (detect_container(b"\xff\xd8\xff") == ContainerType.JPEG):
        raise AssertionError
    if not (detect_container(b"From: john@example.com\n") == ContainerType.EMAIL):
        raise AssertionError
    if not (detect_container(b"random garbage") == ContainerType.UNKNOWN):
        raise AssertionError
    print("Magic detection: PASS")

    # Test 2: Ingest a temp file
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n")
        tmp_path = Path(tmp.name)
    # Make read-only
    os.chmod(tmp_path, 0o444)
    try:
        art = ingest_artifact(tmp_path, case_id="TEST-001", operator="kark")
        if not (art.container_type == ContainerType.PDF):
            raise AssertionError
        if not (art.size_bytes > 0):
            raise AssertionError
        if not (art.filesystem_metadata is not None):
            raise AssertionError
        if not (art.filesystem_metadata.owner_uid == os.getuid()):
            raise AssertionError
        print(f"Artifact ingestion: PASS ({art.artifact_id[:16]}...)")
    finally:
        os.chmod(tmp_path, 0o644)
        tmp_path.unlink()

    # Test 3: Writable file rejection
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp2:
        tmp2.write(b"PK\x03\x04")
        tmp2_path = Path(tmp2.name)
    try:
        ingest_artifact(tmp2_path, case_id="TEST-002", operator="kark")
        print("Writable rejection: FAIL (should have raised)")
        sys.exit(1)
    except IngestionError as exc:
        if "read-only" not in str(exc).lower():
            raise AssertionError from exc
        print(f"Writable rejection: PASS ({exc})")
    finally:
        tmp2_path.unlink()

    print("\nAll ingestion tests passed.")
