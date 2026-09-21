"""
ANCHORUM Forensic Extraction Planes
====================================
Format-specific metadata extraction modules.
"""

from anchorum.forensic.core.extraction.bridge import extract_from_artifact
from anchorum.forensic.core.extraction.email import extract_email_metadata
from anchorum.forensic.core.extraction.image import extract_image_metadata
from anchorum.forensic.core.extraction.pdf import (
    extract_pdf_metadata,
    parse_pdf_date,
)
from anchorum.forensic.core.ooxml_extractor import extract_ooxml_metadata

__all__ = [
    "extract_email_metadata",
    "extract_from_artifact",
    "extract_image_metadata",
    "extract_ooxml_metadata",
    "extract_pdf_metadata",
    "parse_pdf_date",
]
