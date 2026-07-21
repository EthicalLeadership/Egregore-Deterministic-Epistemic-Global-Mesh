"""
OOXML extractor tests.
Stdlib-only: zipfile + xml.etree.
"""

import zipfile
from pathlib import Path

import pytest

from anchorum.forensic.core.ooxml_extractor import extract_ooxml_metadata


@pytest.fixture
def minimal_docx(tmp_path: Path) -> Path:
    """Build a minimal DOCX with metadata, relationships, and content."""
    path = tmp_path / "test.docx"
    with zipfile.ZipFile(path, "w") as zfw:
        zfw.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            '  <Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
            "</Types>",
        )
        zfw.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>\n'
            "</Relationships>",
        )
        zfw.writestr(
            "docProps/core.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            "<cp:coreProperties "
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
            "  <dc:creator>John Smith</dc:creator>\n"
            "  <cp:lastModifiedBy>Jane Doe</cp:lastModifiedBy>\n"
            '  <dcterms:created xsi:type="dcterms:W3CDTF">2024-03-15T10:00:00Z</dcterms:created>\n'
            '  <dcterms:modified xsi:type="dcterms:W3CDTF">2024-03-16T11:30:00Z</dcterms:modified>\n'
            "</cp:coreProperties>",
        )
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
        zfw.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
            'Target="file:///C:/Templates/HR_Grievance.dotx" TargetMode="External"/>\n'
            '  <Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            'Target="https://acme-corp.example.com" TargetMode="External"/>\n'
            "</Relationships>",
        )
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
    return path


class TestOOXMLExtractor:
    def test_application_plane(self, minimal_docx: Path):
        extracted = extract_ooxml_metadata(minimal_docx, artifact_id="ART-OOXML-001")
        app = extracted.plane_application
        assert app is not None
        assert app.author == "John Smith"
        assert app.last_modified_by == "Jane Doe"
        assert app.company == "Acme Corp Canada Inc."
        assert app.application == "Microsoft Office Word"
        assert app.app_version == "16.0000"
        assert app.pages == 2
        assert app.words == 42
        assert app.template == "file:///C:/Templates/HR_Grievance.dotx"

    def test_container_plane(self, minimal_docx: Path):
        extracted = extract_ooxml_metadata(minimal_docx, artifact_id="ART-OOXML-001")
        container = extracted.plane_container
        assert container is not None
        assert len(container.relationships) == 1
        assert container.attached_template == "file:///C:/Templates/HR_Grievance.dotx"
        assert container.core_properties.get("creator") == "John Smith"

    def test_content_plane(self, minimal_docx: Path):
        extracted = extract_ooxml_metadata(minimal_docx, artifact_id="ART-OOXML-001")
        content = extracted.plane_content
        assert content is not None
        assert "john.smith@acme-corp.example.com" in content.email_addresses
        assert "https://acme-corp.example.com" in content.embedded_urls
        assert "https://internal.acme-corp.example.com/claim" in content.embedded_urls
        assert content.word_count == 7
        assert content.hyperlink_count == 2

    def test_temporal_plane(self, minimal_docx: Path):
        extracted = extract_ooxml_metadata(minimal_docx, artifact_id="ART-OOXML-001")
        temporal = extracted.plane_temporal
        assert temporal is not None
        assert temporal.earliest is not None
        assert temporal.latest is not None
        assert temporal.earliest <= temporal.latest
        assert temporal.timezone_count == 0

    def test_artifact_id_passed_through(self, minimal_docx: Path):
        extracted = extract_ooxml_metadata(minimal_docx, artifact_id="ART-OOXML-001")
        assert extracted.artifact_id == "ART-OOXML-001"

    def test_extracted_time_is_utc(self, minimal_docx: Path):
        extracted = extract_ooxml_metadata(minimal_docx, artifact_id="ART-OOXML-001")
        assert extracted.extraction_time.tzinfo is not None
