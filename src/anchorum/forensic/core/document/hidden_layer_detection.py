"""ANCHORUM hidden layer / covert content detection (Plane 4).

Detects optional content layers, embedded files, JavaScript actions, and
annotation objects that may carry obscured information.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pikepdf

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HiddenLayerVerdict:
    optional_content_layers: int = 0
    embedded_files: int = 0
    javascript_actions: int = 0
    annotation_count: int = 0
    total_hidden_layers: int = 0
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "optional_content_layers": self.optional_content_layers,
            "embedded_files": self.embedded_files,
            "javascript_actions": self.javascript_actions,
            "annotation_count": self.annotation_count,
            "total_hidden_layers": self.total_hidden_layers,
            "details": self.details or {},
        }


class HiddenLayerDetector:
    """Detect hidden/covert content inside PDFs."""

    def inspect(self, path: Path | str) -> HiddenLayerVerdict:
        """Inspect a PDF for hidden or covert content."""
        pdf_path = Path(path)
        if not pdf_path.exists():
            return HiddenLayerVerdict(
                details={"error": "file not found"},
            )

        try:
            with pikepdf.open(pdf_path) as pdf:
                oc_layers = self._count_optional_content(pdf.Root)
                embedded = self._count_embedded_files(pdf.Root)
                js_actions, annot_count = self._inspect_pages(pdf.pages)

                total = oc_layers + embedded + js_actions
                return HiddenLayerVerdict(
                    optional_content_layers=oc_layers,
                    embedded_files=embedded,
                    javascript_actions=js_actions,
                    annotation_count=annot_count,
                    total_hidden_layers=total,
                    details={"parser": "pikepdf"},
                )
        except pikepdf.PdfError as exc:
            logger.warning("hidden-layer inspection failed for %s: %s", pdf_path, exc)
            return HiddenLayerVerdict(
                details={"error": f"parse error: {exc}"},
            )

    @staticmethod
    def _count_optional_content(root: Any) -> int:
        oc_props = root.get("/OCProperties")
        if oc_props:
            ocgs = oc_props.get("/OCGs")
            if ocgs:
                return len(ocgs)
        return 0

    @staticmethod
    def _count_embedded_files(root: Any) -> int:
        names = root.get("/Names")
        if names:
            embedded_tree = names.get("/EmbeddedFiles")
            if embedded_tree:
                return HiddenLayerDetector._count_name_tree(embedded_tree)
        return 0

    @staticmethod
    def _inspect_pages(pages: Any) -> tuple[int, int]:
        js_actions = 0
        annot_count = 0
        for page in pages:
            annots = page.get("/Annots")
            if not annots:
                continue
            annot_count += len(annots)
            for annot in annots:  # type: ignore[union-attr]
                try:
                    if annot.get("/Subtype") == "/Widget":
                        action = annot.get("/A")
                        if action and action.get("/S") == "/JavaScript":
                            js_actions += 1
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Skipping unreadable annotation: %s", exc)
                    continue
        return js_actions, annot_count

    @staticmethod
    def _count_name_tree(tree: Any) -> int:
        """Recursively count leaf entries in a PDF name tree."""
        count = 0
        try:
            if tree.get("/Kids"):
                for kid in tree.Kids:
                    count += HiddenLayerDetector._count_name_tree(kid)
            names = tree.get("/Names")
            if names:
                # Names array is key-value pairs; each pair is one entry.
                count += len(names) // 2
        except Exception as exc:  # noqa: BLE001
            logger.debug("Name tree traversal truncated: %s", exc)
        return count
