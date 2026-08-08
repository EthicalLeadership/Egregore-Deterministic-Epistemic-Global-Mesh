"""Port for extracting plain text from office documents.

Domain code declares the interface; infrastructure provides the concrete
adapter that wraps pdfplumber, python-docx, and odfpy.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DocumentTextExtractorPort(Protocol):
    """Extract text and metadata from a document path."""

    def can_extract(self, path: str) -> bool:
        """Return True if the extractor supports this file."""
        ...

    def extract(self, path: str, max_chars: int = 200_000) -> dict[str, Any]:
        """Return {"text": str, "pages": int | None, "error": str | None}."""
        ...
