"""Tests for CUSTOM-006 OCR Confidence Pipeline."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from anchorum.forensic.core.document.ocr_confidence import (
    OcrEnginePort,
    OcrWord,
    TesseractCliEngine,
    _compute_page_confidence,
    _court_admissibility,
    _detect_unusual_confidence_patterns,
    _generate_recommendation,
    _threshold_status,
    analyze_ocr,
)
from anchorum.forensic.core.provenance import clear_events, emitted_events


def _make_image_with_text(path: Path, text: str = "TEST") -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise pytest.skip("Pillow not available") from exc

    img = Image.new("RGB", (200, 60), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except Exception:
        font = ImageFont.load_default()
    draw.text((10, 10), text, fill="black", font=font)
    img.save(path)


def _write_tsv(path: Path, rows: list[list[str]]) -> None:
    lines = [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    ]
    for row in rows:
        lines.append("\t".join(row))
    path.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_events() -> None:
    clear_events()


def test_compute_page_confidence_and_threshold() -> None:
    words = [
        OcrWord("hello", 95.0, (0, 0, 10, 10), "tesseract"),
        OcrWord("world", 85.0, (10, 0, 10, 10), "tesseract"),
    ]
    conf = _compute_page_confidence(words)
    assert 85.0 < conf < 95.0
    assert _threshold_status(conf) == "PASS"

    low = [OcrWord("x", 50.0, (0, 0, 1, 1), "tesseract")]
    assert _threshold_status(_compute_page_confidence(low)) == "FAIL"

    medium = [OcrWord("ok", 75.0, (0, 0, 1, 1), "tesseract")]
    assert _threshold_status(_compute_page_confidence(medium)) == "REVIEW"


def test_detect_bimodal_pattern() -> None:
    words = [OcrWord("a", 95.0, (0, 0, 1, 1), "tesseract") for _ in range(10)] + [
        OcrWord("b", 10.0, (0, 0, 1, 1), "tesseract") for _ in range(10)
    ]
    flags = _detect_unusual_confidence_patterns(words)
    assert any("Bimodal" in f for f in flags)


def test_court_admissibility() -> None:
    assert _court_admissibility("PASS", [])["admissible"] is True
    assert _court_admissibility("FAIL", [])["admissible"] is False
    assert _court_admissibility("PASS", ["anomaly"])["admissible"] is False


def test_generate_recommendation() -> None:
    assert "reliable" in _generate_recommendation("PASS", [])
    assert "manual review" in _generate_recommendation("REVIEW", [])
    assert "insufficient" in _generate_recommendation("FAIL", [])


def test_tesseract_parse_tsv() -> None:
    tsv = Path(tempfile.mktemp(suffix=".tsv"))
    _write_tsv(
        tsv,
        [
            ["5", "1", "1", "1", "1", "1", "10", "20", "30", "40", "96", "Hello"],
            ["5", "1", "1", "1", "1", "2", "50", "20", "35", "40", "88", "world"],
        ],
    )
    engine = TesseractCliEngine()
    words = engine._parse_tsv(tsv)
    tsv.unlink()
    assert len(words) == 2
    assert words[0].text == "Hello"
    assert words[0].confidence == 96.0
    assert words[0].engine == "tesseract"


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_analyze_ocr_real_tesseract(tmp_path: Path) -> None:
    img = tmp_path / "ocr.png"
    _make_image_with_text(img, "OCR TEST")

    ref = analyze_ocr(source=img, case_id="CASE-OCR", operator="tester")
    assert ref.audit_path.exists()
    report = json.loads(ref.audit_path.read_text())["payload"]
    assert report["case_id"] == "CASE-OCR"
    assert report["fallback_chain"][0] == "tesseract"
    assert report["pages"][0]["word_count"] >= 1
    assert len(emitted_events()) == 1


class FakeOcrEngine(OcrEnginePort):
    def __init__(self, words: list[OcrWord]) -> None:
        self._words = words

    def name(self) -> str:
        return "fake"

    def run(self, image_path: Path, language: str) -> list[OcrWord]:
        return self._words


def test_analyze_ocr_uses_fallback(tmp_path: Path) -> None:
    img = tmp_path / "empty.png"
    try:
        from PIL import Image
    except ImportError as exc:
        raise pytest.skip("Pillow not available") from exc
    Image.new("RGB", (10, 10), color="white").save(img)

    fake_words = [OcrWord("fallback", 99.0, (0, 0, 1, 1), "fake")]
    fake = FakeOcrEngine(fake_words)

    with patch.object(TesseractCliEngine, "run", return_value=[]):
        ref = analyze_ocr(
            source=img,
            case_id="CASE-FALLBACK",
            operator="tester",
            fallback_engines=[fake],
        )

    report = json.loads(ref.audit_path.read_text())["payload"]
    assert "fake" in report["fallback_chain"]
    assert report["pages"][0]["word_count"] == 1
    assert report["pages"][0]["words"][0]["text"] == "fallback"
