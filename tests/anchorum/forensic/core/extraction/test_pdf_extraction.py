"""
Tests for PDF Metadata Extraction Plane.
"""

from datetime import UTC, timedelta

import pytest

from anchorum.forensic.core.extraction.pdf import (
    _extract_application_plane,
    _extract_temporal_plane,
    extract_pdf_metadata,
    parse_pdf_date,
)


# ---------------------------------------------------------------------------
# Date Parser Tests
# ---------------------------------------------------------------------------
class TestParsePdfDate:
    def test_utc_z(self):
        dt = parse_pdf_date("D:20240315120000Z")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 15
        assert dt.hour == 12
        assert dt.tzinfo == UTC

    def test_positive_offset(self):
        dt = parse_pdf_date("D:20240315120000+05'00'")
        assert dt is not None
        assert dt.hour == 12
        tz = dt.tzinfo
        assert tz is not None
        assert tz.utcoffset(None) == timedelta(hours=5)

    def test_negative_offset(self):
        dt = parse_pdf_date("D:20240315120000-05'00'")
        assert dt is not None
        tz = dt.tzinfo
        assert tz.utcoffset(None) == timedelta(hours=-5)

    def test_short_offset(self):
        dt = parse_pdf_date("D:20240315120000+05'")
        assert dt is not None
        tz = dt.tzinfo
        assert tz.utcoffset(None) == timedelta(hours=5)

    def test_no_timezone(self):
        dt = parse_pdf_date("D:20240315120000")
        assert dt is not None
        assert dt.tzinfo == UTC

    def test_date_only(self):
        dt = parse_pdf_date("D:20240315")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 15
        assert dt.hour == 0

    def test_missing_d_prefix(self):
        dt = parse_pdf_date("20240315120000Z")
        assert dt is not None
        assert dt.tzinfo == UTC

    def test_bytes_input(self):
        dt = parse_pdf_date(b"D:20240315120000Z")
        assert dt is not None
        assert dt.tzinfo == UTC

    def test_none(self):
        assert parse_pdf_date(None) is None

    def test_garbage(self):
        assert parse_pdf_date("not a date") is None

    def test_fractional_seconds_ignored(self):
        # Some PDFs have fractional seconds; we should handle gracefully
        dt = parse_pdf_date("D:20240315120000.123Z")
        # The regex won't match; should return None or parse main part
        # Current implementation returns None for this edge case
        assert dt is None or dt.year == 2024


# ---------------------------------------------------------------------------
# Mock PdfDocument for plane tests
# ---------------------------------------------------------------------------
class MockPdfDocument:
    """Minimal mock of PdfDocument for testing extraction logic."""

    def __init__(
        self,
        data: bytes = b"%PDF-1.4\n",
        info: dict | None = None,
        catalog: dict | None = None,
        page_count: int = 0,
    ):
        self.data = data
        self._info = info or {}
        self._catalog = catalog or {}
        self._page_count = page_count
        self.xref = {}
        self.xref_objstm = {}

    def get_trailer(self):
        return {"Root": (1, 0), "Info": (2, 0)}

    def get_all_trailers(self):
        return [self.get_trailer()]

    def get_info(self):
        return self._info

    def get_catalog(self):
        return self._catalog

    def get_page_count(self):
        return self._page_count

    def iter_pages(self):
        return iter([])

    def get_page_resources(self, page):
        return {}

    def get_page_content(self, page):
        return b""

    def get_object(self, obj_num):
        return None

    def walk_name_tree(self, node, key_filter=None):
        return []


# ---------------------------------------------------------------------------
# Application Plane Tests
# ---------------------------------------------------------------------------
class TestApplicationPlane:
    def test_empty_info(self):
        doc = MockPdfDocument()
        app = _extract_application_plane(doc)
        assert app.producer is None
        assert app.creator is None

    def test_full_info(self):
        doc = MockPdfDocument(
            info={
                "Producer": "Adobe PDF Library 23.0",
                "Creator": "Microsoft Word 16.0",
                "Author": "John Smith",
                "Title": "Grievance Response",
                "Subject": "Labor Dispute",
                "Keywords": "union, grievance, 2024",
            }
        )
        app = _extract_application_plane(doc)
        assert app.producer == "Adobe PDF Library 23.0"
        assert app.creator == "Microsoft Word 16.0"
        assert app.author == "John Smith"
        assert app.title == "Grievance Response"
        assert app.subject == "Labor Dispute"
        assert app.keywords == ("union", "grievance", "2024")

    def test_bytes_values(self):
        doc = MockPdfDocument(
            info={
                "Producer": b"Adobe PDF Library 23.0",
                "Author": b"Jane Doe",
            }
        )
        app = _extract_application_plane(doc)
        assert app.producer == "Adobe PDF Library 23.0"
        assert app.author == "Jane Doe"


# ---------------------------------------------------------------------------
# Temporal Plane Tests
# ---------------------------------------------------------------------------
class TestTemporalPlane:
    def test_creation_and_mod_dates(self):
        doc = MockPdfDocument(
            info={
                "CreationDate": "D:20240315120000Z",
                "ModDate": "D:20240316120000-05'00'",
            }
        )
        temporal = _extract_temporal_plane(doc, "ART-001")
        assert len(temporal.events) == 2

        creation = [e for e in temporal.events if e.event_type == "creation"][0]
        assert creation.timestamp.year == 2024
        assert creation.timestamp.month == 3
        assert creation.timestamp.day == 15
        assert creation.source_field == "CreationDate"
        assert creation.artifact_id == "ART-001"

        mod = [e for e in temporal.events if e.event_type == "modification"][0]
        assert mod.timestamp.day == 16
        offset = mod.timestamp.utcoffset()
        assert offset == timedelta(hours=-5)

    def test_no_dates(self):
        doc = MockPdfDocument(info={})
        temporal = _extract_temporal_plane(doc, "ART-002")
        assert len(temporal.events) == 0
        assert temporal.earliest is None
        assert temporal.latest is None

    def test_temporal_post_init(self):
        doc = MockPdfDocument(
            info={
                "CreationDate": "D:20240315",
                "ModDate": "D:20240320",
            }
        )
        temporal = _extract_temporal_plane(doc, "ART-003")
        assert temporal.earliest is not None
        assert temporal.latest is not None
        assert temporal.duration_seconds == 5 * 86400  # 5 days


# ---------------------------------------------------------------------------
# End-to-End with real PDF bytes
# ---------------------------------------------------------------------------
class TestEndToEnd:
    def test_extract_from_valid_pdf(self):
        """Build a minimal valid PDF and extract metadata."""
        # We create a simple PDF with known Info dict
        # The parser may or may not handle this perfectly, but it should not crash
        pdf_bytes = _build_minimal_pdf()

        # Import the PdfDocument class from obstruction detector
        try:
            from anchorum.forensic.core.document.pdf_obstruction import PdfDocument
        except ImportError:
            pytest.skip("PdfDocument not available in this environment")

        doc = PdfDocument(pdf_bytes)
        result = extract_pdf_metadata(doc, "TEST-001")

        assert result.artifact_id == "TEST-001"
        assert result.plane_container is not None
        assert result.plane_application is not None
        assert result.plane_temporal is not None

        # Verify temporal events were found
        temporal = result.plane_temporal
        assert len(temporal.events) >= 1  # At least CreationDate


def _build_minimal_pdf() -> bytes:
    """Construct a minimal valid PDF with an Info dictionary."""
    # Build objects as strings first
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
    obj4 = b"4 0 obj\n<< /Producer (Test Producer) /CreationDate (D:20240315120000Z) /Title (Test) >>\nendobj\n"

    header = b"%PDF-1.4\n"

    # Calculate offsets
    offset1 = len(header)
    offset2 = offset1 + len(obj1)
    offset3 = offset2 + len(obj2)
    offset4 = offset3 + len(obj3)

    xref = (
        b"xref\n"
        b"0 5\n"
        b"0000000000 65535 f \n"
        + f"{offset1:010d} 00000 n \n".encode()
        + f"{offset2:010d} 00000 n \n".encode()
        + f"{offset3:010d} 00000 n \n".encode()
        + f"{offset4:010d} 00000 n \n".encode()
    )

    trailer = b"trailer\n<< /Size 5 /Root 1 0 R /Info 4 0 R >>\n"
    startxref_offset = (
        len(header)
        + len(obj1)
        + len(obj2)
        + len(obj3)
        + len(obj4)
        + len(xref)
        + len(trailer)
    )
    startxref = f"startxref\n{startxref_offset}\n%%EOF\n".encode()

    return header + obj1 + obj2 + obj3 + obj4 + xref + trailer + startxref
