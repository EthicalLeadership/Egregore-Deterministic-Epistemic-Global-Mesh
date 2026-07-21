"""
PDF Metadata Extraction Plane
=============================
Extracts all 5 metadata planes from PDFs using the stdlib PdfDocument parser.
Zero dependencies. Feeds into canonicalization engine.

CBI-0:
- M3: Immutable ExtractedMetadata output
- M4: Every timestamp is parsed with raw_value preserved for audit
"""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from anchorum.forensic.core.types import (
    ApplicationMetadata,
    ContainerMetadata,
    ContentMetadata,
    ExtractedMetadata,
    TemporalEvent,
    TemporalMetadata,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. PDF Date Parser
# ---------------------------------------------------------------------------
_PDF_DATE_RE = re.compile(
    r"""
    (\d{4})(\d{2})(\d{2})          # YYYYMMDD
    (?:(\d{2})(\d{2})(\d{2}))?     # HHMMSS (optional)
    (?:(Z|[+-])(\d{2})'?(\d{2})?')? # timezone: Z or +HH'MM' or -HH'MM'
    """,
    re.VERBOSE,
)

_PDF_DATE_SHORT_RE = re.compile(r"(\d{4})(\d{2})(\d{2})")


def parse_pdf_date(raw: str | bytes | None) -> datetime | None:
    """
    Parse PDF date string to timezone-aware datetime.

    Handles:
    - D:20240315120000Z
    - D:20240315120000+05'00'
    - D:20240315120000-05'00'
    - D:20240315120000+05'
    - D:20240315
    - 20240315120000 (missing D: prefix)
    """
    if raw is None:
        return None
    s = raw.decode("latin-1") if isinstance(raw, bytes) else str(raw)
    s = s.strip()
    if s.startswith("D:"):
        s = s[2:]

    m = _PDF_DATE_RE.match(s)
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
        day = int(m.group(3))
        hour = int(m.group(4) or 0)
        minute = int(m.group(5) or 0)
        second = int(m.group(6) or 0)
        tz_sign = m.group(7)
        tz_h = m.group(8) or "00"
        tz_m = m.group(9) or "00"

        dt = datetime(year, month, day, hour, minute, second)
        if tz_sign == "Z":
            return dt.replace(tzinfo=UTC)
        offset_seconds = int(tz_h) * 3600 + int(tz_m) * 60
        if tz_sign == "-":
            offset_seconds = -offset_seconds
        tz = timezone(timedelta(seconds=offset_seconds))
        return dt.replace(tzinfo=tz)

    # Date only: YYYYMMDD
    m = _PDF_DATE_SHORT_RE.match(s)
    if m:
        return datetime(
            int(m.group(1)),
            int(m.group(2)),
            int(m.group(3)),
            tzinfo=UTC,
        )

    return None


# ---------------------------------------------------------------------------
# 2. Main Extraction API
# ---------------------------------------------------------------------------
def extract_pdf_metadata(doc: Any, artifact_id: str) -> ExtractedMetadata:
    """
    Extract all 5 metadata planes from a PdfDocument.

    Args:
        doc: PdfDocument instance (from pdf_obstruction or similar).
             Must have: data, get_trailer, get_all_trailers, get_info,
             get_catalog, get_page_count, iter_pages, get_page_resources,
             get_page_content, get_object, walk_name_tree.
        artifact_id: The artifact ID for provenance tracking.

    Returns:
        ExtractedMetadata with all planes populated.

    """
    extraction_time = datetime.now(UTC)

    container = _extract_container_plane(doc)
    application = _extract_application_plane(doc)
    content = _extract_content_plane(doc)
    temporal = _extract_temporal_plane(doc, artifact_id)

    return ExtractedMetadata(
        artifact_id=artifact_id,
        extraction_time=extraction_time,
        plane_container=container,
        plane_application=application,
        plane_content=content,
        plane_temporal=temporal,
    )


# ---------------------------------------------------------------------------
# 3. Plane Extractors
# ---------------------------------------------------------------------------
def _extract_container_plane(doc: Any) -> ContainerMetadata:  # noqa: C901
    """Extract PDF structural metadata (Plane 2)."""
    header = (
        doc.data[:32].decode("latin-1", errors="ignore") if hasattr(doc, "data") else ""
    )
    version_match = re.search(r"%PDF-(\d+\.\d+)", header)
    pdf_version = version_match.group(1) if version_match else None

    all_trailers = doc.get_all_trailers() if hasattr(doc, "get_all_trailers") else []

    encrypt_revisions = tuple(t.get("Encrypt") is not None for t in all_trailers)

    # JavaScript detection
    js_locs: list[str] = []
    cat = doc.get_catalog() if hasattr(doc, "get_catalog") else None
    if isinstance(cat, dict):
        names = cat.get("Names")
        if isinstance(names, dict):
            js_tree = names.get("JavaScript")
            if hasattr(doc, "walk_name_tree"):
                js_locs.extend(doc.walk_name_tree(js_tree))
        if cat.get("OpenAction") is not None:
            js_locs.append("/OpenAction")
        if cat.get("AA") is not None:
            js_locs.append("/AA")

    # Embedded files
    emb_files: list[str] = []
    if isinstance(cat, dict):
        names = cat.get("Names")
        if isinstance(names, dict):
            emb_tree = names.get("EmbeddedFiles")
            if hasattr(doc, "walk_name_tree"):
                emb_files.extend(doc.walk_name_tree(emb_tree))
        af = cat.get("AF")
        if isinstance(af, list):
            emb_files.append(f"AssociatedFiles ({len(af)} refs)")

    # AcroForm / XFA
    acro_fields = 0
    xfa = False
    if isinstance(cat, dict):
        acroform = cat.get("AcroForm")
        if isinstance(acroform, dict):
            acro_fields = len(acroform.get("Fields", []))
            xfa = acroform.get("XFA") is not None
        elif isinstance(acroform, tuple) and hasattr(doc, "get_object"):
            form_obj = doc.get_object(acroform[0])
            if isinstance(form_obj, dict):
                acro_fields = len(form_obj.get("Fields", []))
                xfa = form_obj.get("XFA") is not None

    # Cross-reference integrity
    xref_count = len(getattr(doc, "xref", {}))
    xref_count += len(getattr(doc, "xref_objstm", {}))
    xref_corrupt = xref_count == 0 and len(all_trailers) > 0

    # Linearized check
    data_prefix = doc.data[:4096] if hasattr(doc, "data") else b""
    linearized = b"/Linearized" in data_prefix

    return ContainerMetadata(
        format_version="PDF",
        pdf_header_version=pdf_version,
        xref_count=xref_count,
        object_streams=len(getattr(doc, "xref_objstm", {})),
        incremental_updates=max(0, len(all_trailers) - 1),
        trailer_count=len(all_trailers),
        encrypt_revisions=encrypt_revisions,
        javascript_locations=tuple(js_locs),
        embedded_files=tuple(emb_files),
        acroform_fields=acro_fields,
        xfa_detected=xfa,
        cross_reference_corruption=xref_corrupt,
        linearized=linearized,
    )


def _extract_application_plane(doc: Any) -> ApplicationMetadata:
    """Extract PDF Info dictionary metadata (Plane 3)."""
    info = doc.get_info() if hasattr(doc, "get_info") else None
    if not isinstance(info, dict):
        return ApplicationMetadata()

    def _get_str(key: str) -> str | None:
        val = info.get(key)
        if val is None:
            return None
        if isinstance(val, bytes):
            return val.decode("latin-1")
        return str(val)

    def _parse_keywords(kw: Any) -> tuple[str, ...]:
        if kw is None:
            return ()
        if isinstance(kw, bytes):
            kw = kw.decode("latin-1")
        if isinstance(kw, str):
            return tuple(k.strip() for k in kw.split(",") if k.strip())
        return ()

    return ApplicationMetadata(
        producer=_get_str("Producer"),
        creator=_get_str("Creator"),
        author=_get_str("Author"),
        title=_get_str("Title"),
        subject=_get_str("Subject"),
        keywords=_parse_keywords(info.get("Keywords")),
        company=_get_str("Company"),
        last_modified_by=_get_str("ModAuthor"),
    )


def _extract_text_from_content(content: bytes) -> str:
    """Extract literal strings from a PDF content stream."""
    texts: list[str] = []
    # Literal strings: balanced parentheses
    for m in re.finditer(rb"\(([^()]*(?:\([^()]*\)[^()]*)*)\)", content):
        with contextlib.suppress(Exception):
            texts.append(m.group(1).decode("latin-1", errors="ignore"))
    # Hex strings
    for m in re.finditer(rb"<([0-9A-Fa-f ]+)>", content):
        with contextlib.suppress(Exception):
            hex_part = m.group(1).replace(b" ", b"")
            decoded = bytes.fromhex(hex_part.decode("ascii", errors="ignore"))
            if b"\x00" in decoded:
                texts.append(decoded.decode("utf-16-be", errors="ignore"))
            else:
                texts.append(decoded.decode("latin-1", errors="ignore"))
    return " ".join(texts)


def _extract_content_plane(doc: Any) -> ContentMetadata:  # noqa: C901
    """Extract content-derived metadata (Plane 4)."""
    page_count = doc.get_page_count() if hasattr(doc, "get_page_count") else 0

    fonts: set[str] = set()
    image_count = 0
    urls: set[str] = set()
    emails: set[str] = set()
    all_text: list[str] = []

    if hasattr(doc, "iter_pages"):
        for page in doc.iter_pages():
            if hasattr(doc, "get_page_resources"):
                res = doc.get_page_resources(page)
                if isinstance(res, dict):
                    # Fonts
                    font_dict = res.get("Font")
                    if isinstance(font_dict, dict):
                        for _font_name, font_ref in font_dict.items():
                            base = None
                            if isinstance(font_ref, tuple) and hasattr(
                                doc, "get_object"
                            ):
                                font_obj = doc.get_object(font_ref[0])
                                if isinstance(font_obj, dict):
                                    base = font_obj.get("BaseFont")
                            elif isinstance(font_ref, dict):
                                base = font_ref.get("BaseFont")
                            if base:
                                fonts.add(str(base).lstrip("/"))

                    # Images
                    xobj = res.get("XObject")
                    if isinstance(xobj, dict):
                        for _name, ref in xobj.items():
                            subtype = None
                            if isinstance(ref, tuple) and hasattr(doc, "get_object"):
                                img = doc.get_object(ref[0])
                                if isinstance(img, dict):
                                    subtype = img.get("Subtype")
                            elif isinstance(ref, dict):
                                subtype = ref.get("Subtype")
                            if subtype == "/Image":
                                image_count += 1

            # URLs / emails from page content
            if hasattr(doc, "get_page_content"):
                content = doc.get_page_content(page)
                if isinstance(content, bytes):
                    text = _extract_text_from_content(content)
                    all_text.append(text)
                    urls.update(
                        re.findall(
                            r"https?://[^\s<>\"{}|\\^`\[\]]+", text, re.IGNORECASE
                        )
                    )
                    urls.update(
                        re.findall(r"ftp://[^\s<>\"{}|\\^`\[\]]+", text, re.IGNORECASE)
                    )
                    emails.update(
                        re.findall(
                            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text
                        )
                    )

    words = " ".join(all_text).split()
    return ContentMetadata(
        fonts=tuple(sorted(fonts)),
        font_families=tuple(sorted(fonts)),
        page_count=page_count,
        image_count=image_count,
        embedded_urls=tuple(sorted(urls)),
        email_addresses=tuple(sorted(emails)),
        word_count=len(words) if words else None,
        character_count=sum(len(t) for t in all_text) if all_text else None,
        hyperlink_count=len(urls),
    )


def _extract_temporal_plane(doc: Any, artifact_id: str) -> TemporalMetadata:
    """Extract all timestamps from PDF metadata (Plane 5)."""
    events: list[TemporalEvent] = []

    info = doc.get_info() if hasattr(doc, "get_info") else None
    if isinstance(info, dict):
        for field_name, event_type in (
            ("CreationDate", "creation"),
            ("ModDate", "modification"),
        ):
            raw = info.get(field_name)
            dt = parse_pdf_date(raw)
            if dt:
                events.append(
                    TemporalEvent(
                        timestamp=dt,
                        event_type=event_type,
                        source_plane="container",
                        source_field=field_name,
                        raw_value=str(raw) if raw else "",
                        timezone=dt.tzname(),
                        confidence=1.0,
                        artifact_id=artifact_id,
                    )
                )

    return TemporalMetadata(
        events=tuple(events),
        timezone_count=len({e.timezone for e in events if e.timezone}),
        timezone_names=tuple(sorted({e.timezone for e in events if e.timezone})),
    )
