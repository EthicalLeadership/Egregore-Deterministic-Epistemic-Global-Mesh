"""Concrete document text extractor using pdfplumber, python-docx, and odfpy."""

from __future__ import annotations

import logging
from typing import Any

from egregore.interface.document_extraction_port import DocumentTextExtractorPort

# Suppress noisy pdfminer logging.
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)


def _suffix(path: str) -> str:
    """Return the lower-case file extension without the leading dot."""
    if "." not in path:
        return ""
    return path.rsplit(".", 1)[-1].lower()


class DocumentTextExtractorAdapter(DocumentTextExtractorPort):
    """Extract text from office documents using available libraries."""

    SUPPORTED_SUFFIXES: frozenset[str] = frozenset({"pdf", "docx", "doc", "odt"})

    def can_extract(self, path: str) -> bool:
        return _suffix(path) in self.SUPPORTED_SUFFIXES

    def extract(self, path: str, max_chars: int = 200_000) -> dict[str, Any]:
        """Extract text and metadata from a document.

        Returns {"text": str, "pages": int | None, "error": str | None}.
        """
        suffix = _suffix(path)
        if suffix == "pdf":
            return self._extract_pdf(path, max_chars)
        if suffix in ("docx", "doc"):
            return self._extract_docx(path, max_chars)
        if suffix == "odt":
            return self._extract_odt(path, max_chars)
        return {"text": "", "pages": None, "error": f"Unsupported format: {suffix}"}

    def _extract_pdf(self, path: str, max_chars: int) -> dict[str, Any]:
        try:
            import pdfplumber
        except ImportError as exc:  # pragma: no cover
            return {
                "text": "",
                "pages": None,
                "error": f"pdfplumber not available: {exc}",
            }

        parts: list[str] = []
        page_count = 0
        try:
            with pdfplumber.open(path) as pdf:
                page_count = len(pdf.pages)
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        parts.append(text)
                    if sum(len(p) for p in parts) >= max_chars:
                        break
        except Exception as exc:
            return {"text": "\n".join(parts), "pages": page_count, "error": str(exc)}

        return {
            "text": "\n".join(parts)[:max_chars],
            "pages": page_count,
            "error": None,
        }

    def _extract_odt(self, path: str, max_chars: int) -> dict[str, Any]:
        try:
            from odf import opendocument
            from odf.text import P
        except ImportError as exc:  # pragma: no cover
            return {"text": "", "pages": None, "error": f"odfpy not available: {exc}"}

        def _get_text(node) -> str:
            """Recursively extract text from an ODF node."""
            parts: list[str] = []
            for child in node.childNodes:
                if hasattr(child, "data"):
                    parts.append(child.data)
                elif hasattr(child, "childNodes"):
                    parts.append(_get_text(child))
            return "".join(parts)

        try:
            doc = opendocument.load(path)
            paragraphs = doc.getElementsByType(P)
            texts: list[str] = []
            for p in paragraphs:
                txt = _get_text(p).strip()
                if txt:
                    texts.append(txt)
            full_text = "\n".join(texts)[:max_chars]
            return {"text": full_text, "pages": None, "error": None}
        except Exception as exc:
            return {"text": "", "pages": None, "error": str(exc)}

    def _extract_docx(self, path: str, max_chars: int) -> dict[str, Any]:
        try:
            import docx
        except ImportError as exc:  # pragma: no cover
            return {
                "text": "",
                "pages": None,
                "error": f"python-docx not available: {exc}",
            }

        try:
            document = docx.Document(path)
            paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
            text = "\n".join(paragraphs)[:max_chars]
            return {"text": text, "pages": None, "error": None}
        except Exception as exc:
            return {"text": "", "pages": None, "error": str(exc)}
