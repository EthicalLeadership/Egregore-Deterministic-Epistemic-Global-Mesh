"""ANCHORUM PDF classification engine (Plane 1).

Classifies PDFs by file type, redaction markers, scanned-page heuristics,
and page count. Uses permissively-licensed ``pikepdf`` / ``pdfminer.six``;
no GPL-only tools and no PyMuPDF (``fitz``) in core.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pikepdf
from pdfminer.high_level import extract_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentVerdict:
    input_path: Path
    file_type: str
    is_redacted: bool
    is_scanned: bool
    page_count: int
    classification_confidence: float
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_path": str(self.input_path),
            "file_type": self.file_type,
            "is_redacted": self.is_redacted,
            "is_scanned": self.is_scanned,
            "page_count": self.page_count,
            "classification_confidence": self.classification_confidence,
            "details": self.details or {},
        }


class PdfPharosEngine:
    """Classify PDFs for ANCHORUM Plane 1."""

    def classify(self, path: Path | str) -> DocumentVerdict:
        pdf_path = Path(path)
        if not pdf_path.exists():
            return DocumentVerdict(
                input_path=pdf_path,
                file_type="unknown",
                is_redacted=False,
                is_scanned=False,
                page_count=0,
                classification_confidence=0.0,
                details={"error": "file not found"},
            )

        try:
            with pikepdf.open(pdf_path) as pdf:
                page_count = len(pdf.pages)
                is_redacted = self._has_redaction_annotations(pdf)
                is_scanned = self._looks_scanned(pdf_path, page_count)
                confidence = 0.95 if page_count else 0.5
                return DocumentVerdict(
                    input_path=pdf_path,
                    file_type="pdf",
                    is_redacted=is_redacted,
                    is_scanned=is_scanned,
                    page_count=page_count,
                    classification_confidence=confidence,
                    details={"parser": "pikepdf", "encrypted": pdf.is_encrypted},
                )
        except pikepdf.PdfError as exc:
            logger.warning("pikepdf failed for %s: %s", pdf_path, exc)
            return DocumentVerdict(
                input_path=pdf_path,
                file_type="pdf",
                is_redacted=False,
                is_scanned=False,
                page_count=0,
                classification_confidence=0.0,
                details={"error": f"pikepdf parse error: {exc}"},
            )

    def _has_redaction_annotations(self, pdf: pikepdf.Pdf) -> bool:
        for page in pdf.pages:
            annots = page.get("/Annots")
            if not annots:
                continue
            for annot in annots:  # type: ignore[union-attr]
                try:
                    if annot.get("/Subtype") == "/Redact":
                        return True
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Skipping unreadable annotation: %s", exc)
                    continue
        return False

    def _looks_scanned(self, path: Path, page_count: int) -> bool:
        """Heuristic: mostly image-based with little extractable text."""
        if page_count == 0:
            return False
        try:
            text = extract_text(str(path), maxpages=min(page_count, 3))
            text_len = len(text.strip())
        except Exception as exc:  # noqa: BLE001
            logger.debug("Text extraction failed for %s: %s", path, exc)
            text_len = 0
        # Fewer than ~20 visible characters across sampled pages suggests scanned.
        return text_len < 20
