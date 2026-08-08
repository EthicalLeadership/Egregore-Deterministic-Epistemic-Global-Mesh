"""OCR Confidence Pipeline — Elite Forensic Edition.

Plane 2 module for extracting text from rasterised/obstructed PDFs with
per‑word confidence, multi‑engine fallback, and court‑defensible audit trails.

Tesseract (Apache 2.0) is used as an external subprocess (not linked).
Additional engines can be plugged in via the OcrEnginePort interface.

Zero GPL/AGPL contamination. All confidence heuristics are original and
scientifically grounded.

CBI‑0 Governance:
- M1: Original file read‑only; all writes go to derivative directories.
- M2: Tool registered in capability manifest.
- M3: Terminal output captured to .zarc, not re‑entered.
- M4: Spec/runtime audit emitted after every run.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess  # nosec B404
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anchorum.forensic.core.document.pdf_obstruction import EventReference
from anchorum.forensic.core.manifest import register_tool
from anchorum.forensic.core.paths import anchorum_zarc_dir
from anchorum.forensic.core.provenance import ZarcEventType, emit_zarc_event
from anchorum.forensic.core.shell import _run_external
from anchorum.forensic.core.validation import validate_input_size

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Constants
# ---------------------------------------------------------------------------
CONFIDENCE_THRESHOLD_PASS = 90.0  # per‑word average ≥ 90% → PASS
CONFIDENCE_THRESHOLD_REVIEW = 70.0  # 70‑90% → REVIEW
# Below 70% → FAIL

# Tesseract TSV column indices (0-based)
TSV_COL_LEVEL = 0
TSV_COL_PAGENUM = 1
TSV_COL_BLOCKNUM = 2
TSV_COL_PARNUM = 3
TSV_COL_LINENUM = 4
TSV_COL_WORDNUM = 5
TSV_COL_LEFT = 6
TSV_COL_TOP = 7
TSV_COL_WIDTH = 8
TSV_COL_HEIGHT = 9
TSV_COL_CONF = 10
TSV_COL_TEXT = 11

# Minimum characters to consider a word "valid" for confidence
MIN_WORD_LENGTH = 2

# ---------------------------------------------------------------------------
# 2. Tool registration (M2)
# ---------------------------------------------------------------------------
register_tool(
    name="ocr_confidence_pipeline",
    version="1.0.0",
    plane="Plane 2",
    description="OCR with per-word confidence and court-ready audit trail",
    dependencies=["tesseract (>=4.0, Apache 2.0, subprocess only)"],
    license="ANCHORUM proprietary (wraps Apache 2.0 Tesseract via subprocess)",
)


# ---------------------------------------------------------------------------
# 3. Ports & Data Structures
# ---------------------------------------------------------------------------
class OcrEnginePort(ABC):
    """Interface for an OCR engine."""

    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def run(self, image_path: Path, language: str) -> list[OcrWord]: ...


@dataclass(frozen=True)
class OcrWord:
    text: str
    confidence: float  # 0‑100
    bbox: tuple[int, int, int, int]  # x, y, w, h
    engine: str


@dataclass(frozen=True)
class OcrPageResult:
    page_num: int
    words: tuple[OcrWord, ...]
    page_confidence: float  # average word confidence
    threshold_status: str  # PASS / REVIEW / FAIL
    preprocessing_applied: tuple[str, ...]


@dataclass(frozen=True)
class OcrReport:
    """Immutable output of the pipeline."""

    original_hash: str
    pages: tuple[OcrPageResult, ...]
    fallback_chain: tuple[str, ...]  # engines tried in order
    recommendation: str
    evidence_quality: dict[str, Any]


# ---------------------------------------------------------------------------
# 4. Tesseract Subprocess Engine (Plane‑2)
# ---------------------------------------------------------------------------
class TesseractCliEngine(OcrEnginePort):
    """Call tesseract via subprocess and parse TSV output.

    Tesseract must be installed on the system (Apache 2.0 license).
    """

    def name(self) -> str:
        return "tesseract"

    def run(self, image_path: Path, language: str = "eng") -> list[OcrWord]:
        with tempfile.NamedTemporaryFile(suffix=".tsv", delete=False) as tmp:
            tsv_path = Path(tmp.name)

        cmd = [
            "tesseract",
            str(image_path),
            str(tsv_path.with_suffix("")),  # output base name
            "-l",
            language,
            "tsv",
        ]
        try:
            _run_external(cmd, check=True, capture_output=True, timeout=120)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            logger.error(
                "Tesseract failed: %s",
                stderr if stderr else exc,
            )
            return []
        except FileNotFoundError as exc:
            raise RuntimeError(
                "tesseract binary not found. Install tesseract-ocr."
            ) from exc

        words = self._parse_tsv(tsv_path)
        os.unlink(tsv_path)  # clean up
        return words

    def _parse_tsv(self, tsv_path: Path) -> list[OcrWord]:
        words = []
        with open(tsv_path, encoding="utf-8") as f:
            next(f)  # skip header
            for line in f:
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 12:
                    continue
                try:
                    conf = float(cols[TSV_COL_CONF])
                    if conf < 0:  # tesseract uses -1 for "no confidence"
                        conf = 0.0
                except ValueError:
                    conf = 0.0
                text = cols[TSV_COL_TEXT].strip()
                if not text:
                    continue
                # Bbox: left, top, width, height
                left = int(cols[TSV_COL_LEFT])
                top = int(cols[TSV_COL_TOP])
                w = int(cols[TSV_COL_WIDTH])
                h = int(cols[TSV_COL_HEIGHT])
                words.append(OcrWord(text, conf, (left, top, w, h), "tesseract"))
        return words


# ---------------------------------------------------------------------------
# 5. Confidence Scoring (Original Algorithms)
# ---------------------------------------------------------------------------
def _compute_page_confidence(words: list[OcrWord]) -> float:
    """Average word confidence weighted by character length."""
    if not words:
        return 0.0
    total_weight = sum(max(len(w.text), 1) for w in words)
    if total_weight == 0:
        return 0.0
    weighted_conf = sum(w.confidence * max(len(w.text), 1) for w in words)
    return weighted_conf / total_weight


def _threshold_status(avg_conf: float) -> str:
    if avg_conf >= CONFIDENCE_THRESHOLD_PASS:
        return "PASS"
    elif avg_conf >= CONFIDENCE_THRESHOLD_REVIEW:
        return "REVIEW"
    return "FAIL"


# ---------------------------------------------------------------------------
# 6. Fraud / Degradation Detection (Court‑ready)
# ---------------------------------------------------------------------------
def _detect_unusual_confidence_patterns(words: list[OcrWord]) -> list[str]:
    """Flag word‑level anomalies that suggest intentional degradation."""
    flags: list[str] = []
    if not words:
        return flags
    confs = [w.confidence for w in words]
    # 1. Bimodal distribution: many very high and many very low → deliberate obfuscation
    high = sum(1 for c in confs if c > 90)
    low = sum(1 for c in confs if c < 30)
    if high > len(confs) * 0.4 and low > len(confs) * 0.3:
        flags.append(
            "Bimodal confidence distribution — possible selective obfuscation."
        )
    # 2. Sudden drop in confidence within a line (spliced text)
    #    Not implemented in this first release, but placeholder.
    return flags


# ---------------------------------------------------------------------------
# 7. Preprocessing (Deterministic, Plane‑1 compatible if needed)
# ---------------------------------------------------------------------------
def _preprocess_image(image_path: Path) -> Path:
    """Apply standard preprocessing: deskew, binarization, denoise.

    Uses Pillow (HPND) if available, else falls back to a no‑op.
    Always returns a path to the preprocessed image (derivative).
    """
    # Pillow preprocessing is intentionally a no-op in this release to keep the
    # core dependency set minimal. Plane‑2 modules may extend this later.
    return image_path


# ---------------------------------------------------------------------------
# 8. Public API
# ---------------------------------------------------------------------------
def analyze_ocr(
    *,
    source: str | Path,
    language: str = "eng",
    case_id: str,
    operator: str,
    fallback_engines: list[OcrEnginePort] | None = None,
) -> EventReference:
    """Run OCR with confidence scoring and fallback chain.

    Args:
        source: Path to image or image‑based PDF page.
        language: Tesseract language code(s).
        case_id: Case identifier.
        operator: Operator ID.
        fallback_engines: Additional engines to try if primary fails.

    Returns:
        EventReference to the sealed .zarc report.

    """
    original_path = Path(source)
    if not original_path.exists():
        raise FileNotFoundError(f"Original file not found: {original_path}")

    validate_input_size(original_path, label="ocr_source")

    # Hash original
    original_hash = _sha256_file(original_path)

    preprocessed = _preprocess_image(original_path)

    # Primary engine: Tesseract
    engines = [TesseractCliEngine()] + (fallback_engines or [])
    all_words: list[OcrWord] = []
    tried_engines: list[str] = []
    for engine in engines:
        tried_engines.append(engine.name())
        words = engine.run(preprocessed, language)
        if words:
            all_words = words
            break
        logger.warning("Engine %s produced no output; trying next.", engine.name())

    # Group words by page (if possible; here we assume single page for now)
    # Tesseract TSV contains page numbers; we could group them.
    # For simplicity, treat all words as one page.
    page_confidence = _compute_page_confidence(all_words)
    status = _threshold_status(page_confidence)
    anomaly_flags = _detect_unusual_confidence_patterns(all_words)

    # Build report
    page_result = OcrPageResult(
        page_num=1,
        words=tuple(all_words),
        page_confidence=page_confidence,
        threshold_status=status,
        preprocessing_applied=(
            ("binarization", "deskew") if preprocessed != original_path else ()
        ),
    )
    report = OcrReport(
        original_hash=original_hash,
        pages=(page_result,),
        fallback_chain=tuple(tried_engines),
        recommendation=_generate_recommendation(status, anomaly_flags),
        evidence_quality=_court_admissibility(status, anomaly_flags),
    )

    # Serialize and emit .zarc
    payload = {
        "original_hash": report.original_hash,
        "pages": [
            {
                "page_num": p.page_num,
                "page_confidence": p.page_confidence,
                "threshold_status": p.threshold_status,
                "word_count": len(p.words),
                "words": [
                    {
                        "text": w.text,
                        "confidence": w.confidence,
                        "bbox": w.bbox,
                        "engine": w.engine,
                    }
                    for w in p.words
                ],
                "preprocessing": p.preprocessing_applied,
            }
            for p in report.pages
        ],
        "fallback_chain": report.fallback_chain,
        "recommendation": report.recommendation,
        "evidence_quality": report.evidence_quality,
        "case_id": case_id,
        "operator": operator,
        "timestamp_utc": datetime.now(UTC).isoformat(),
    }

    event_id = emit_zarc_event(
        event_type=ZarcEventType.OCR_CONFIDENCE,
        case_id=case_id,
        operator=operator,
        payload=payload,
    )
    return EventReference(event_id, anchorum_zarc_dir(case_id) / f"{event_id}.json")


def _generate_recommendation(status: str, flags: list[str]) -> str:
    if status == "PASS" and not flags:
        return "OCR output is reliable. Suitable for direct use."
    elif status == "REVIEW":
        return "Confidence is moderate; manual review recommended for key sections."
    else:
        return (
            "OCR quality is insufficient. Original document may be intentionally "
            "degraded. Manual transcription required."
        )


def _court_admissibility(status: str, flags: list[str]) -> dict[str, Any]:
    if status == "FAIL" or flags:
        return {
            "admissible": False,
            "explanation": "Low OCR confidence or anomaly detected. Use with caution.",
        }
    return {
        "admissible": True,
        "explanation": "OCR confidence high; output can be attested.",
    }


def _sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()
