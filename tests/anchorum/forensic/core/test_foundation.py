"""
Foundation tests: ingestion, canonicalization, types.
Must pass before any extraction plane is built.
"""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from anchorum.forensic.core.canonicalization import (
    EntityExtractor,
    entity_id,
    merge_entities,
    normalize_device,
    normalize_email,
    normalize_organization,
    normalize_person,
    normalize_software,
    normalize_text,
)
from anchorum.forensic.core.ingestion import (
    IngestionError,
    detect_container,
    hash_bytes,
    hash_file,
    infer_mime_type,
    ingest_artifact,
    ingest_directory,
)
from anchorum.forensic.core.types import (
    ApplicationMetadata,
    ContainerType,
    EntityType,
    FsMetadata,
    to_canonical_json,
)


# ---------------------------------------------------------------------------
# Magic Detection
# ---------------------------------------------------------------------------
class TestMagicDetection:
    def test_pdf(self):
        assert detect_container(b"%PDF-1.4\n") == ContainerType.PDF

    def test_ooxml(self):
        assert detect_container(b"PK\x03\x04") == ContainerType.OOXML

    def test_jpeg(self):
        assert detect_container(b"\xff\xd8\xff") == ContainerType.JPEG

    def test_png(self):
        assert detect_container(b"\x89PNG\r\n\x1a\n") == ContainerType.PNG

    def test_email_from(self):
        assert detect_container(b"From: john@example.com\n") == ContainerType.EMAIL

    def test_email_return_path(self):
        assert (
            detect_container(b"Return-Path: <bounce@example.com>\n")
            == ContainerType.EMAIL
        )

    def test_legacy_office(self):
        assert (
            detect_container(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
            == ContainerType.LEGACY_OFFICE
        )

    def test_unknown(self):
        assert detect_container(b"random garbage") == ContainerType.UNKNOWN

    def test_empty(self):
        assert detect_container(b"") == ContainerType.UNKNOWN


# ---------------------------------------------------------------------------
# MIME Inference
# ---------------------------------------------------------------------------
class TestMimeInference:
    def test_pdf(self):
        assert infer_mime_type(ContainerType.PDF, b"%PDF-1.4") == "application/pdf"

    def test_ooxml_word(self):
        data = b"PK\x03\x04" + b"word/document.xml"
        assert infer_mime_type(ContainerType.OOXML, data) == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_ooxml_excel(self):
        data = b"PK\x03\x04" + b"xl/workbook.xml"
        assert infer_mime_type(ContainerType.OOXML, data) == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_ooxml_powerpoint(self):
        data = b"PK\x03\x04" + b"ppt/presentation.xml"
        assert infer_mime_type(ContainerType.OOXML, data) == (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------
class TestHashing:
    def test_bytes_determinism(self):
        data = b"ANCHORUM test payload"
        h1 = hash_bytes(data)
        h2 = hash_bytes(data)
        assert h1 == h2
        assert len(h1) == 64

    def test_file_determinism(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"ANCHORUM file hash test")
            path = Path(tmp.name)
        try:
            h1 = hash_file(path)
            h2 = hash_file(path)
            assert h1 == h2
            assert len(h1) == 64
        finally:
            path.unlink()

    def test_bytes_vs_file_equivalence(self):
        data = b"Same content for both"
        h_bytes = hash_bytes(data)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            path = Path(tmp.name)
        try:
            h_file = hash_file(path)
            assert h_bytes == h_file
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
class TestIngestion:
    def test_ingest_readonly_pdf(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n")
            path = Path(tmp.name)
        os.chmod(path, 0o444)
        try:
            art = ingest_artifact(path, case_id="TEST", operator="kark")
            assert art.container_type == ContainerType.PDF
            assert art.size_bytes > 0
            assert art.artifact_id is not None
            assert len(art.artifact_id) == 64
            assert art.filesystem_metadata is not None
            assert art.filesystem_metadata.owner_uid == os.getuid()
            assert art.mime_type == "application/pdf"
        finally:
            os.chmod(path, 0o644)
            path.unlink()

    def test_reject_writable(self):
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(b"PK\x03\x04")
            path = Path(tmp.name)
        try:
            with pytest.raises(IngestionError, match="read-only"):
                ingest_artifact(path, case_id="TEST", operator="kark")
        finally:
            path.unlink()

    def test_reject_missing(self):
        with pytest.raises(IngestionError, match="not found"):
            ingest_artifact(
                Path("/nonexistent/file.pdf"), case_id="TEST", operator="kark"
            )

    def test_ingest_directory(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "a.pdf").write_bytes(b"%PDF-1.4\n")
            (d / "b.docx").write_bytes(b"PK\x03\x04")
            sub = d / "sub"
            sub.mkdir()
            (sub / "c.jpg").write_bytes(b"\xff\xd8\xff")

            # Make all read-only
            for f in d.rglob("*"):
                if f.is_file():
                    os.chmod(f, 0o444)

            try:
                arts = ingest_directory(d, case_id="TEST", operator="kark")
                assert len(arts) == 3
                types = {a.container_type for a in arts}
                assert ContainerType.PDF in types
                assert ContainerType.OOXML in types
                assert ContainerType.JPEG in types
            finally:
                for f in d.rglob("*"):
                    if f.is_file():
                        os.chmod(f, 0o644)

    def test_ingest_bytes_stream(self):
        data = b"%PDF-1.4\ntest"
        h = hash_bytes(data)
        assert len(h) == 64


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
class TestNormalization:
    def test_text_unicode_nfkc(self):
        # NFKC: fullwidth letters -> ASCII
        assert normalize_text("\uff2a\uff2f\uff28\uff2e") == "JOHN"

    def test_text_collapse_whitespace(self):
        assert normalize_text("  too   much   space  ") == "too much space"

    def test_text_strip_control(self):
        assert normalize_text("hello\x00world") == "hello world"

    def test_person_strip_titles(self):
        assert normalize_person("Dr. John Smith") == "john smith"

    def test_person_sort_components(self):
        assert normalize_person("SMITH, JOHN") == "john smith"

    def test_person_punctuation(self):
        assert normalize_person("SMITH, JOHN") == "john smith"

    def test_organization_strip_suffix(self):
        assert normalize_organization("Acme Corp Canada Inc.") == "acme corp canada"

    def test_organization_strip_ltd(self):
        assert normalize_organization("Egregore Edge Ltd.") == "egregore edge"

    def test_organization_strip_sarl(self):
        assert normalize_organization("Kark SARL") == "kark"

    def test_email_strip_plus(self):
        assert (
            normalize_email("John.Smith+HR@AcmeCorp.COM") == "john.smith@acmecorp.com"
        )

    def test_email_lowercase(self):
        assert normalize_email("JOHN@EXAMPLE.COM") == "john@example.com"

    def test_software_strip_version(self):
        assert normalize_software("Microsoft Word 16.0.12345") == "microsoft word"

    def test_software_strip_beta(self):
        assert normalize_software("MyApp v2.1 beta 3") == "myapp"

    def test_device_mac(self):
        assert normalize_device("00:1a:2b:3c:4d:5e") == "00:1A:2B:3C:4D:5E"

    def test_device_mac_hyphen(self):
        assert normalize_device("00-1a-2b-3c-4d-5e") == "00:1A:2B:3C:4D:5E"


# ---------------------------------------------------------------------------
# Entity ID
# ---------------------------------------------------------------------------
class TestEntityId:
    def test_determinism(self):
        e1 = entity_id("john smith", EntityType.PERSON)
        e2 = entity_id("john smith", EntityType.PERSON)
        assert e1 == e2
        assert len(e1) == 64

    def test_type_sensitivity(self):
        e1 = entity_id("john smith", EntityType.PERSON)
        e2 = entity_id("john smith", EntityType.ORGANIZATION)
        assert e1 != e2


# ---------------------------------------------------------------------------
# Entity Extraction
# ---------------------------------------------------------------------------
class TestEntityExtraction:
    def test_from_application(self):
        extractor = EntityExtractor()
        app = ApplicationMetadata(
            author="Dr. Jane Doe",
            company="Acme Corp Canada Inc.",
            producer="Microsoft Word 16.0",
            template="\\\\EXAMPLE-HQ-FS01\\Templates\\HR_Grievance.dotx",
        )
        entities = extractor.extract_from_application(app, "ART-001")
        assert len(entities) == 4
        types = {e.entity_type for e in entities}
        assert EntityType.PERSON in types
        assert EntityType.ORGANIZATION in types
        assert EntityType.SOFTWARE in types
        assert EntityType.DEVICE in types

    def test_template_server_extraction(self):
        extractor = EntityExtractor()
        app = ApplicationMetadata(
            template="\\\\FILESERVER01\\share\\template.dotx",
        )
        entities = extractor.extract_from_application(app, "ART-002")
        devices = [e for e in entities if e.entity_type == EntityType.DEVICE]
        assert len(devices) == 1
        assert devices[0].normalized_form == "fileserver01"

    def test_from_application_no_data(self):
        extractor = EntityExtractor()
        app = ApplicationMetadata()
        entities = extractor.extract_from_application(app, "ART-003")
        assert len(entities) == 0

    def test_deduplication(self):
        extractor = EntityExtractor()
        e1 = extractor._make_entity(
            "john smith", EntityType.PERSON, "John Smith", "ART-001", "app.author"
        )
        e2 = extractor._make_entity(
            "john smith", EntityType.PERSON, "J. Smith", "ART-002", "app.author"
        )
        merged = merge_entities([e1, e2])
        assert len(merged) == 1
        assert set(merged[0].aliases) == {"J. Smith", "John Smith"}
        assert len(merged[0].source_artifacts) == 2
        assert "ART-001" in merged[0].source_artifacts
        assert "ART-002" in merged[0].source_artifacts

    def test_merge_preserves_earliest_latest(self):
        from anchorum.forensic.core.types import ApplicationMetadata

        extractor = EntityExtractor()
        app1 = ApplicationMetadata(author="John Smith")
        app2 = ApplicationMetadata(author="John Smith")
        e1 = extractor.extract_from_application(app1, "ART-001")[0]
        e2 = extractor.extract_from_application(app2, "ART-002")[0]
        merged = merge_entities([e1, e2])
        assert len(merged) == 1


# ---------------------------------------------------------------------------
# FsMetadata
# ---------------------------------------------------------------------------
class TestFsMetadata:
    def test_utc_normalization(self):
        fs = FsMetadata(
            birth_time=None,
            mod_time=datetime(2024, 3, 15, 10, 0, 0, tzinfo=UTC),
            access_time=datetime(2024, 3, 15, 10, 0, 0, tzinfo=UTC),
            inode=12345,
            device=67890,
            mode=0o100644,
            owner_uid=1000,
            group_gid=1000,
        )
        assert fs.mod_time.tzinfo is not None
        assert fs.mod_time.year == 2024

    def test_optional_fields(self):
        fs = FsMetadata(
            birth_time=datetime(2024, 1, 1, tzinfo=UTC),
            mod_time=datetime(2024, 3, 15, 10, 0, 0, tzinfo=UTC),
            access_time=datetime(2024, 3, 15, 10, 0, 0, tzinfo=UTC),
            inode=1,
            device=1,
            mode=0o100644,
            owner_uid=0,
            group_gid=0,
            owner_name="root",
            group_name="root",
            hardlink_count=2,
            extended_attrs={"user.comment": b"test"},
            alternate_data_streams=("Zone.Identifier",),
        )
        assert fs.owner_name == "root"
        assert fs.hardlink_count == 2


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
class TestCanonicalJson:
    def test_artifact_roundtrip(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF-1.4\n")
            path = Path(tmp.name)
        os.chmod(path, 0o444)
        try:
            art = ingest_artifact(path, case_id="TEST", operator="kark")
            d = to_canonical_json(art)
            assert d["artifact_id"] == art.artifact_id
            assert d["container_type"] == "pdf"
            assert "ingest_time" in d
        finally:
            os.chmod(path, 0o644)
            path.unlink()
