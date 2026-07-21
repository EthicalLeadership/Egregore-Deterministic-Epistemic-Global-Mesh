"""ANCHORUM digital signature detection (Plane 3).

Detects PDF signature fields and reports counts. Actual CMS validation is
intentionally delegated to the cryptographic module in a future sprint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pikepdf

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignatureVerdict:
    signed_count: int = 0
    unsigned_count: int = 0
    has_expired: bool = False
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "signed_count": self.signed_count,
            "unsigned_count": self.unsigned_count,
            "has_expired": self.has_expired,
            "details": self.details or {},
        }


class SignaturePharos:
    """Detect signature fields in PDFs."""

    def inspect(self, path: Path | str) -> SignatureVerdict:
        pdf_path = Path(path)
        if not pdf_path.exists():
            return SignatureVerdict(
                unsigned_count=1,
                details={"error": "file not found"},
            )

        try:
            with pikepdf.open(pdf_path) as pdf:
                signed = 0
                unsigned = 0
                acro_form = pdf.Root.get("/AcroForm")
                if acro_form:
                    fields = acro_form.get("/Fields")
                    if fields:
                        for field in fields:  # type: ignore[union-attr]
                            try:
                                ft = field.get("/FT")
                            except Exception as exc:  # noqa: BLE001
                                logger.debug("Skipping unreadable field: %s", exc)
                                continue
                            if ft == "/Sig":
                                v = field.get("/V")
                                if v:
                                    signed += 1
                                else:
                                    unsigned += 1
                return SignatureVerdict(
                    signed_count=signed,
                    unsigned_count=unsigned,
                    details={"signature_fields_found": signed + unsigned},
                )
        except pikepdf.PdfError as exc:
            logger.warning("signature inspection failed for %s: %s", pdf_path, exc)
            return SignatureVerdict(
                unsigned_count=1,
                details={"error": f"parse error: {exc}"},
            )
