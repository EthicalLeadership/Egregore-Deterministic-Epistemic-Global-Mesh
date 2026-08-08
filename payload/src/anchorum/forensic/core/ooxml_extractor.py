"""
ANCHORUM OOXML Metadata Extractor
=================================
Stdlib-only extraction of DOCX/XLSX/PPTX metadata.
CBI-0 governed: read-only input, immutable output.

Extracts:
- Container plane: core properties, relationships, comments, revisions
- Application plane: application name, version, company, manager, stats
- Temporal plane: created, modified, print, comment, revision timestamps
- Content plane: fonts, embedded URLs, emails, word/page counts
"""

from __future__ import annotations

import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from xml.etree import ElementTree as ET

from anchorum.forensic.core.types import (
    ApplicationMetadata,
    Artifact,
    Comment,
    ContainerMetadata,
    ContentMetadata,
    ExtractedMetadata,
    Relationship,
    Revision,
    TemporalEvent,
    TemporalMetadata,
)

# ---------------------------------------------------------------------------
# 1. Namespaces
# ---------------------------------------------------------------------------
_NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

# Strip namespace URIs from tag names for easier reading
_QNAME_RE = re.compile(r"\{[^}]+\}")

# Relationship types we care about
_REL_TEMPLATE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate"
_REL_HYPERLINK = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)
_REL_CORE_DOCS = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
}

# URL/email regex (basic)
_URL_RE = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


# ---------------------------------------------------------------------------
# 2. Helpers
# ---------------------------------------------------------------------------
def _local_name(tag: str) -> str:
    return _QNAME_RE.sub("", tag)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _int_or_none(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _text_of(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    return element.text


def _xml_from_zip(zf: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        with zf.open(name) as f:
            return ET.parse(f).getroot()  # noqa: S314
    except (KeyError, ET.ParseError):
        return None


def _read_text_from_zip(zf: zipfile.ZipFile, name: str) -> str:
    try:
        with zf.open(name) as f:
            return f.read().decode("utf-8", errors="replace")
    except KeyError:
        return ""


# ---------------------------------------------------------------------------
# 3. Relationship Parsing
# ---------------------------------------------------------------------------
def _parse_relationships(
    zf: zipfile.ZipFile, rels_path: str
) -> tuple[Relationship, ...]:
    root = _xml_from_zip(zf, rels_path)
    if root is None:
        return ()
    rels: list[Relationship] = []
    for child in root:
        if _local_name(child.tag) != "Relationship":
            continue
        rel_id = child.get("Id", "")
        rel_type = child.get("Type", "")
        target = child.get("Target", "")
        target_mode = child.get("TargetMode")
        rels.append(
            Relationship(
                rel_id=rel_id,
                rel_type=rel_type,
                target=target,
                target_mode=target_mode,
            )
        )
    return tuple(rels)


def _find_document_part(zf: zipfile.ZipFile) -> str | None:
    """Return the main document part path (e.g., word/document.xml)."""
    rels = _parse_relationships(zf, "_rels/.rels")
    for rel in rels:
        if rel.rel_type in _REL_CORE_DOCS:
            target = rel.target
            # Relative to package root
            if target.startswith("/"):
                target = target[1:]
            return target
    # Fallback by presence
    for name in ("word/document.xml", "xl/workbook.xml", "ppt/presentation.xml"):
        if name in zf.namelist():
            return name
    return None


def _rels_path_for(part_path: str) -> str:
    """Given 'word/document.xml', return 'word/_rels/document.xml.rels'."""
    p = Path(part_path)
    return str(p.parent / "_rels" / (p.name + ".rels"))


# ---------------------------------------------------------------------------
# 4. Core / App Properties
# ---------------------------------------------------------------------------
def _extract_core_properties(zf: zipfile.ZipFile) -> dict[str, Any]:
    root = _xml_from_zip(zf, "docProps/core.xml")
    props: dict[str, Any] = {}
    if root is None:
        return props
    for child in root:
        tag = _local_name(child.tag)
        text = child.text
        if text is None:
            continue
        text = text.strip()
        if not text:
            continue
        if tag in ("created", "modified"):
            props[tag] = _parse_iso_datetime(text)
        elif tag == "lastModifiedBy":
            props["last_modified_by"] = text
        elif tag == "revision":
            props["revision"] = _int_or_none(text)
        else:
            props[tag] = text
    return props


def _extract_app_properties(zf: zipfile.ZipFile) -> dict[str, Any]:
    root = _xml_from_zip(zf, "docProps/app.xml")
    props: dict[str, Any] = {}
    if root is None:
        return props
    tag_to_field = {
        "Application": "application",
        "AppVersion": "app_version",
        "Company": "company",
        "Manager": "manager",
        "Pages": "pages",
        "Words": "words",
        "Characters": "characters",
        "Paragraphs": "paragraphs",
        "Lines": "lines",
        "Category": "category",
        "Title": "title",
        "Subject": "subject",
        "Keywords": "keywords",
    }
    for child in root:
        tag = _local_name(child.tag)
        key = tag_to_field.get(tag)
        if key is None:
            continue
        text = child.text
        if text is None:
            continue
        text = text.strip()
        if not text:
            continue
        if key in ("pages", "words", "characters", "paragraphs", "lines"):
            props[key] = _int_or_none(text)
        else:
            props[key] = text
    return props


# ---------------------------------------------------------------------------
# 5. Document Content
# ---------------------------------------------------------------------------
def _extract_text_from_document(zf: zipfile.ZipFile, doc_path: str) -> str:
    root = _xml_from_zip(zf, doc_path)
    if root is None:
        return ""
    texts: list[str] = []
    for elem in root.iter():
        if _local_name(elem.tag) == "t" and elem.text:
            texts.append(elem.text)
    return " ".join(texts)


def _extract_fonts(zf: zipfile.ZipFile) -> tuple[str, ...]:
    """Best-effort font extraction from word/fontTable.xml."""
    root = _xml_from_zip(zf, "word/fontTable.xml")
    if root is None:
        return ()
    fonts: set[str] = set()
    for elem in root.iter():
        if _local_name(elem.tag) == "font":
            name = elem.get("{{{}}}name".format(_NS["w"])) or elem.get("name")
            if name:
                fonts.add(name)
    return tuple(sorted(fonts))


def _extract_comments(zf: zipfile.ZipFile) -> tuple[Comment, ...]:
    root = _xml_from_zip(zf, "word/comments.xml")
    if root is None:
        return ()
    comments: list[Comment] = []
    for elem in root:
        if _local_name(elem.tag) != "comment":
            continue
        author = elem.get("{{{}}}author".format(_NS["w"])) or elem.get("author")
        date_str = elem.get("{{{}}}date".format(_NS["w"])) or elem.get("date")
        initials = elem.get("{{{}}}initials".format(_NS["w"])) or elem.get("initials")
        cid = elem.get("{{{}}}id".format(_NS["w"])) or elem.get("id") or ""
        texts: list[str] = []
        for t in elem.iter():
            if _local_name(t.tag) == "t" and t.text:
                texts.append(t.text)
        comments.append(
            Comment(
                comment_id=str(cid),
                author=author,
                date=_parse_iso_datetime(date_str),
                text=" ".join(texts) or None,
                initials=initials,
            )
        )
    return tuple(comments)


def _extract_revisions(zf: zipfile.ZipFile) -> tuple[Revision, ...]:
    """Extract tracked revision authors/dates from word/document.xml."""
    root = _xml_from_zip(zf, "word/document.xml")
    if root is None:
        return ()
    revisions: list[Revision] = []
    seen: set[tuple[str, str | None]] = set()
    for elem in root.iter():
        tag = _local_name(elem.tag)
        if tag not in ("ins", "del"):
            continue
        author = elem.get("{{{}}}author".format(_NS["w"])) or elem.get("author")
        date_str = elem.get("{{{}}}date".format(_NS["w"])) or elem.get("date")
        key = (author or "", date_str)
        if key in seen:
            continue
        seen.add(key)
        revisions.append(
            Revision(
                rev_id=f"{tag}-{len(revisions)}",
                author=author,
                date=_parse_iso_datetime(date_str),
                rev_type="insertion" if tag == "ins" else "deletion",
            )
        )
    return tuple(revisions)


# ---------------------------------------------------------------------------
# 6. Temporal Plane
# ---------------------------------------------------------------------------
def _build_temporal_plane(
    core_props: dict[str, Any],
    comments: tuple[Comment, ...],
    revisions: tuple[Revision, ...],
    artifact_id: str,
) -> TemporalMetadata:
    events: list[TemporalEvent] = []

    def _add(
        event_type: str, source_field: str, raw: str | None, ts: datetime | None
    ) -> None:
        if ts is None or raw is None:
            return
        events.append(
            TemporalEvent(
                timestamp=ts,
                event_type=event_type,
                source_plane="container",
                source_field=source_field,
                raw_value=raw,
                timezone=ts.tzname(),
                confidence=1.0,
                artifact_id=artifact_id,
            )
        )

    for key, event_type in (("created", "creation"), ("modified", "modification")):
        raw = core_props.get(key)
        if isinstance(raw, datetime):
            _add(event_type, f"core.{key}", raw.isoformat(), raw)
        elif isinstance(raw, str):
            ts = _parse_iso_datetime(raw)
            _add(event_type, f"core.{key}", raw, ts)

    for c in comments:
        if c.date:
            _add("comment", "comment.date", c.date.isoformat(), c.date)

    for r in revisions:
        if r.date:
            _add("revision", f"revision.{r.rev_type}", r.date.isoformat(), r.date)

    return TemporalMetadata(events=tuple(events))


# ---------------------------------------------------------------------------
# 7. Main Extractor
# ---------------------------------------------------------------------------
def extract_ooxml_metadata(
    source: Artifact | Path | str | BinaryIO,
    artifact_id: str | None = None,
) -> ExtractedMetadata:
    """
    Extract all 5 metadata planes from an OOXML file.

    Args:
        source: Artifact, path, or readable binary stream.
        artifact_id: Required when source is a stream; optional otherwise.

    Returns:
        Immutable ExtractedMetadata.

    """
    if isinstance(source, Artifact):
        path = Path(source.source_path)
        aid = source.artifact_id
    elif isinstance(source, (Path, str)):
        path = Path(source)
        aid = artifact_id or path.name
    else:
        path = None
        aid = artifact_id or "stream"

    extraction_time = datetime.now(UTC)

    if path is not None:
        zf = zipfile.ZipFile(path, "r")
    else:
        zf = zipfile.ZipFile(source, "r")

    with zf:
        doc_path = _find_document_part(zf)
        core_props = _extract_core_properties(zf)
        app_props = _extract_app_properties(zf)
        relationships = _parse_relationships(zf, "_rels/.rels")
        comments = _extract_comments(zf)
        revisions = _extract_revisions(zf)

        # Per-document relationships
        doc_rels: tuple[Relationship, ...] = ()
        if doc_path:
            doc_rels = _parse_relationships(zf, _rels_path_for(doc_path))

        # Template
        template: str | None = None
        for rel in doc_rels:
            if rel.rel_type == _REL_TEMPLATE:
                template = rel.target
                break

        # Hyperlinks
        hyperlinks: list[str] = []
        for rel in doc_rels:
            if rel.rel_type == _REL_HYPERLINK and rel.target_mode == "External":
                hyperlinks.append(rel.target)

        # Text content for content plane
        text = ""
        if doc_path:
            text = _extract_text_from_document(zf, doc_path)

        fonts = _extract_fonts(zf)

    # Build application metadata
    application_metadata = ApplicationMetadata(
        creator=core_props.get("creator"),
        author=core_props.get("creator"),
        last_modified_by=core_props.get("last_modified_by"),
        title=core_props.get("title"),
        subject=core_props.get("subject"),
        keywords=(
            tuple(core_props.get("keywords", "").split(", "))
            if core_props.get("keywords")
            else ()
        ),
        application=app_props.get("application"),
        app_version=app_props.get("app_version"),
        company=app_props.get("company"),
        manager=app_props.get("manager"),
        pages=app_props.get("pages"),
        words=app_props.get("words"),
        characters=app_props.get("characters"),
        paragraphs=app_props.get("paragraphs"),
        lines=app_props.get("lines"),
        template=template,
    )

    # Build container metadata
    container_metadata = ContainerMetadata(
        format_version=app_props.get("app_version"),
        core_properties=core_props,
        extended_properties=app_props,
        relationships=relationships,
        revision_history=revisions,
        comments=comments,
        embedded_objects=tuple(hyperlinks),
        attached_template=template,
    )

    # Build content metadata
    all_urls = set(hyperlinks)
    all_urls.update(_URL_RE.findall(text))
    all_emails = set(_EMAIL_RE.findall(text))
    word_count = len(text.split()) if text else None
    char_count = len(text) if text else None

    content_metadata = ContentMetadata(
        fonts=fonts,
        embedded_urls=tuple(sorted(all_urls)),
        email_addresses=tuple(sorted(all_emails)),
        word_count=word_count,
        character_count=char_count,
        page_count=app_props.get("pages"),
        hyperlink_count=len(all_urls),
        annotation_count=len(comments),
    )

    # Build temporal metadata
    temporal_metadata = _build_temporal_plane(core_props, comments, revisions, aid)

    return ExtractedMetadata(
        artifact_id=aid,
        extraction_time=extraction_time,
        plane_container=container_metadata,
        plane_application=application_metadata,
        plane_content=content_metadata,
        plane_temporal=temporal_metadata,
    )


# ---------------------------------------------------------------------------
# 8. Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile

    from anchorum.forensic.core.types import to_canonical_json

    # Build a minimal DOCX in memory
    docx_bytes = b"""PK\x03\x04
"""
    # A real DOCX requires valid ZIP structure; self-test uses a hand-built file.
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    import zipfile as zf_mod

    with zf_mod.ZipFile(tmp_path, "w") as zfw:
        # Content_Types.xml
        zfw.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
            "</Types>",
        )
        # _rels/.rels
        zfw.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
            "</Relationships>",
        )
        # docProps/core.xml
        zfw.writestr(
            "docProps/core.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
            "  <dc:creator>John Smith</dc:creator>\n"
            "  <cp:lastModifiedBy>Jane Doe</cp:lastModifiedBy>\n"
            '  <dcterms:created xsi:type="dcterms:W3CDTF">2024-03-15T10:00:00Z</dcterms:created>\n'
            '  <dcterms:modified xsi:type="dcterms:W3CDTF">2024-03-16T11:30:00Z</dcterms:modified>\n'
            "</cp:coreProperties>",
        )
        # docProps/app.xml
        zfw.writestr(
            "docProps/app.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">\n'
            "  <Application>Microsoft Office Word</Application>\n"
            "  <AppVersion>16.0000</AppVersion>\n"
            "  <Company>Acme Corp Canada Inc.</Company>\n"
            "  <Pages>2</Pages>\n"
            "  <Words>42</Words>\n"
            "</Properties>",
        )
        # word/_rels/document.xml.rels
        zfw.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" Target="file:///C:/Templates/HR_Grievance.dotx" TargetMode="External"/>\n'
            '  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://acme-corp.example.com" TargetMode="External"/>\n'
            "</Relationships>",
        )
        # word/document.xml
        zfw.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
            "  <w:body>\n"
            "    <w:p><w:r><w:t>Confidential memo from john.smith@acme-corp.example.com</w:t></w:r></w:p>\n"
            "    <w:p><w:r><w:t>See also https://internal.acme-corp.example.com/claim</w:t></w:r></w:p>\n"
            "  </w:body>\n"
            "</w:document>",
        )

    extracted = extract_ooxml_metadata(tmp_path, artifact_id="TEST-DOCX-001")
    if extracted.plane_application is None:
        raise AssertionError
    if not (extracted.plane_application.author == "John Smith"):
        raise AssertionError
    if not (extracted.plane_application.company == "Acme Corp Canada Inc."):
        raise AssertionError
    if not (extracted.plane_application.application == "Microsoft Office Word"):
        raise AssertionError
    if extracted.plane_container is None:
        raise AssertionError
    if not (len(extracted.plane_container.relationships) == 1):
        raise AssertionError
    if extracted.plane_content is None:
        raise AssertionError
    if (
        "john.smith@acme-corp.example.com"
        not in extracted.plane_content.email_addresses
    ):
        raise AssertionError
    if "https://acme-corp.example.com" not in extracted.plane_content.embedded_urls:
        raise AssertionError
    if extracted.plane_temporal is None:
        raise AssertionError
    if extracted.plane_temporal.earliest is None:
        raise AssertionError
    print("OOXML extraction self-test: PASS")
    print(to_canonical_json(extracted.plane_application))

    tmp_path.unlink()
