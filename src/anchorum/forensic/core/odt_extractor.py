"""ANCHORUM ODT (OpenDocument Text) metadata extractor.

Deterministic extraction of metadata and plain text from ODT files.
Uses odfpy for content extraction; falls back gracefully if unavailable.

CBI-0 governed: read-only source access, immutable output.
"""

from __future__ import annotations

import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anchorum.forensic.core.types import (
    ApplicationMetadata,
    Artifact,
    ContainerMetadata,
    ContentMetadata,
    ExtractedMetadata,
    TemporalEvent,
    TemporalMetadata,
)

logger = logging.getLogger(__name__)


def _parse_odf_datetime(value: str | None) -> datetime | None:
    """Parse ODF date/time strings (ISO-8601 subset)."""
    if not value:
        return None
    # ODF dates may include timezone offsets like 2026-04-15T09:00:01+00:00
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            continue
    return None


def _extract_text_from_odt(path: Path) -> str:
    """Extract plain text from an ODT file using odfpy."""
    try:
        from odf import opendocument
        from odf.text import P
    except ImportError as exc:
        raise RuntimeError("odfpy is not installed") from exc

    doc = opendocument.load(str(path))

    def _get_text(node) -> str:
        parts: list[str] = []
        for child in node.childNodes:
            if hasattr(child, "data"):
                parts.append(child.data)
            elif hasattr(child, "childNodes"):
                parts.append(_get_text(child))
        return "".join(parts)

    paragraphs = doc.getElementsByType(P)
    texts: list[str] = []
    for p in paragraphs:
        txt = _get_text(p).strip()
        if txt:
            texts.append(txt)
    return "\n".join(texts)


def _read_meta_xml(path: Path) -> dict[str, Any]:
    """Parse meta.xml from the ODT ZIP for Dublin Core / ODF metadata."""
    meta: dict[str, Any] = {}
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "meta.xml" in zf.namelist():
                data = zf.read("meta.xml")
                # Lightweight XML parsing — no heavy deps
                import xml.etree.ElementTree as ET

                root = ET.fromstring(data)  # noqa: S314
                ns = {
                    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
                    "dc": "http://purl.org/dc/elements/1.1/",
                    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
                }

                # Simple text extraction from known meta fields
                for tag, key in (
                    (".//dc:title", "title"),
                    (".//dc:subject", "subject"),
                    (".//dc:description", "description"),
                    (".//dc:creator", "creator"),
                    (".//meta:initial-creator", "initial_creator"),
                    (".//meta:creation-date", "creation_date"),
                    (".//dc:date", "date"),
                    (".//meta:print-date", "print_date"),
                    (".//meta:editing-cycles", "editing_cycles"),
                    (".//meta:editing-duration", "editing_duration"),
                    (".//meta:document-statistic/meta:page-count", "page_count"),
                    (".//meta:document-statistic/meta:word-count", "word_count"),
                    (
                        ".//meta:document-statistic/meta:character-count",
                        "character_count",
                    ),
                    (
                        ".//meta:document-statistic/meta:paragraph-count",
                        "paragraph_count",
                    ),
                ):
                    elem = root.find(tag, ns)
                    if elem is not None and elem.text:
                        meta[key] = elem.text
    except Exception as exc:
        logger.debug("meta.xml extraction failed for %s: %s", path, exc)

    return meta


def extract_odt_metadata(artifact: Artifact) -> ExtractedMetadata:
    """Extract all metadata planes from an ODT artifact.

    Returns ExtractedMetadata with container, application, content, and temporal
    planes populated from the ODT file.
    """
    path = Path(artifact.source_path)
    text = ""
    try:
        text = _extract_text_from_odt(path)
    except Exception as exc:
        logger.warning(
            "Text extraction failed for ODT %s: %s", artifact.artifact_id, exc
        )

    meta = _read_meta_xml(path)

    # Build temporal events
    temporal_events: list[TemporalEvent] = []
    for field, event_type in (
        ("creation_date", "creation"),
        ("date", "modification"),
        ("print_date", "print"),
    ):
        raw = meta.get(field)
        if raw:
            dt = _parse_odf_datetime(raw)
            if dt:
                temporal_events.append(
                    TemporalEvent(
                        timestamp=dt,
                        event_type=event_type,
                        source_plane="container",
                        source_field=field,
                        raw_value=raw,
                        artifact_id=artifact.artifact_id,
                    )
                )

    temporal = TemporalMetadata(
        events=tuple(temporal_events),
        timezone_count=1 if temporal_events else 0,
        timezone_names=("UTC",) if temporal_events else (),
    )

    # Word / character counts from meta or fallback to text
    word_count = None
    char_count = None
    para_count = None
    page_count = None
    try:
        word_count = int(meta.get("word_count", 0)) or None
        char_count = int(meta.get("character_count", 0)) or None
        para_count = int(meta.get("paragraph_count", 0)) or None
        page_count = int(meta.get("page_count", 0)) or None
    except (ValueError, TypeError):
        pass

    # Fallback: count from extracted text if meta missing
    if word_count is None and text:
        word_count = len(text.split())
    if char_count is None and text:
        char_count = len(text)
    if para_count is None and text:
        para_count = len([p for p in text.split("\n") if p.strip()])

    content = ContentMetadata(
        word_count=word_count,
        character_count=char_count,
        line_count=para_count,
        page_count=page_count,
        language_detected=(
            "fr" if any(c in text for c in "éèàùâêîôû") else "en" if text else None
        ),
        language_confidence=0.7 if text else None,
    )

    application = ApplicationMetadata(
        creator=meta.get("creator") or meta.get("initial_creator"),
        title=meta.get("title"),
        subject=meta.get("subject"),
        keywords=(
            tuple(meta.get("description", "").split(", "))
            if meta.get("description")
            else ()
        ),
        total_editing_time_minutes=None,
        app_version=None,
        platform=None,
        application="odfpy" if text else None,
    )

    container = ContainerMetadata(
        format_version="ODF 1.2",
        object_count=para_count,
        stream_count=1,
    )

    return ExtractedMetadata(
        artifact_id=artifact.artifact_id,
        extraction_time=datetime.now(UTC),
        plane_fs=artifact.filesystem_metadata,
        plane_container=container,
        plane_application=application,
        plane_content=content,
        plane_temporal=temporal,
        extraction_errors=(),
    )
