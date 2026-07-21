"""Tests for the Steganography Statistical Detector."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anchorum.forensic.core.document.steganography_detector import (
    StegoToolPort,
    ToolResult,
    _entropy_analysis,
    _generate_lsb_plane,
    _lsb_chi_square,
    _read_rgb_pixels,
    detect_steganography,
)
from anchorum.forensic.core.provenance import clear_events, emitted_events


def _make_image(path: Path, pixels: list[tuple[int, int, int]], width: int) -> None:
    try:
        from PIL import Image
    except ImportError as exc:
        raise pytest.skip("Pillow not available") from exc

    height = len(pixels) // width
    img = Image.new("RGB", (width, height))
    img.putdata(pixels[: width * height])
    img.save(path)


@pytest.fixture(autouse=True)
def _clear_events() -> None:
    clear_events()


def test_lsb_chi_square_constant_image() -> None:
    # Constant colour → very non‑uniform LSB pairs
    pixels = [(128, 128, 128)] * 100
    lsb = _lsb_chi_square(pixels)
    assert 0.0 <= lsb.p_value <= 1.0
    assert lsb.embedded_data_detected


def test_lsb_chi_square_uniform_lsb() -> None:
    # Construct pixels whose channel values alternate even/odd evenly
    pixels = []
    for i in range(256):
        pixels.append((i, i, i))
    lsb = _lsb_chi_square(pixels)
    assert 0.0 <= lsb.p_value <= 1.0
    # Should not detect for this roughly uniform distribution
    assert not lsb.embedded_data_detected


def test_entropy_low_for_constant_image() -> None:
    pixels = [(128, 128, 128)] * 64
    ent = _entropy_analysis(pixels, 8, 8)
    assert ent.global_entropy < 1.0
    assert ent.local_entropy_variance == 0.0


def test_entropy_high_for_random_image(tmp_path: Path) -> None:
    try:
        from PIL import Image
    except ImportError as exc:
        raise pytest.skip("Pillow not available") from exc

    img_path = tmp_path / "random.png"
    img = Image.effect_noise((32, 32), 128).convert("RGB")
    img.save(img_path)
    pixels, width, height = _read_rgb_pixels(img_path)
    ent = _entropy_analysis(pixels, width, height)
    assert ent.global_entropy > 5.0


def test_generate_lsb_plane(tmp_path: Path) -> None:
    pixels = [(255, 0, 128)] * 16
    path_str = _generate_lsb_plane(pixels, 4, 4, tmp_path)
    path = Path(path_str)
    assert path.exists()
    assert path.read_bytes().startswith(b"P5\n4 4\n255\n")


def test_read_rgb_pixels_png(tmp_path: Path) -> None:
    img_path = tmp_path / "test.png"
    _make_image(img_path, [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)], 2)
    pixels, width, height = _read_rgb_pixels(img_path)
    assert width == 2
    assert height == 2
    assert len(pixels) == 4


class FakeStegoTool(StegoToolPort):
    def __init__(self, detected: bool, output: str = "") -> None:
        self._detected = detected
        self._output = output

    def name(self) -> str:
        return "fake_stego"

    def detect(self, image_path: Path) -> ToolResult:
        return ToolResult(self.name(), self._detected, self._output)


def test_detect_steganography_with_fake_tool(tmp_path: Path) -> None:
    img_path = tmp_path / "cover.png"
    _make_image(
        img_path, [(i % 256, (i * 2) % 256, (i * 3) % 256) for i in range(64)], 8
    )

    ref = detect_steganography(
        source=img_path,
        case_id="CASE-STEGO",
        operator="tester",
        external_tools=[FakeStegoTool(detected=True, output="suspicious")],
    )
    assert ref.audit_path.exists()
    report = json.loads(ref.audit_path.read_text())["payload"]
    assert report["case_id"] == "CASE-STEGO"
    assert any(t["tool"] == "fake_stego" for t in report["tool_results"])
    assert report["confidence"] in ("LOW", "MEDIUM", "HIGH")
    assert Path(report["visual_attack_path"]).exists()
    assert len(emitted_events()) == 1


def test_detect_steganography_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        detect_steganography(
            source=tmp_path / "missing.png",
            case_id="CASE-MISSING",
            operator="tester",
        )
