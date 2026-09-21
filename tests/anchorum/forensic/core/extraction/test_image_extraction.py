"""Tests for ANCHORUM image metadata extractor."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest

from anchorum.forensic.core.extraction.image import extract_image_metadata

try:
    from PIL import Image

    PILLOW_AVAILABLE = True
except Exception:
    PILLOW_AVAILABLE = False


def _make_test_jpeg(
    width: int = 100,
    height: int = 80,
    dt_original: str | None = "2023:05:10 14:30:00",
) -> bytes:
    """Create a minimal JPEG with optional EXIF using Pillow."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow not installed")

    img = Image.new("RGB", (width, height), color=(128, 64, 32))
    buf = io.BytesIO()

    if dt_original:
        from PIL.ExifTags import Base

        exif = Image.Exif()
        exif[Base.DateTimeOriginal] = dt_original
        exif[Base.Make] = "TestCamera"
        exif[Base.Model] = "TC-1"
        exif[Base.Software] = "TestSoftware 1.0"
        img.save(buf, format="JPEG", exif=exif.tobytes())
    else:
        img.save(buf, format="JPEG")

    return buf.getvalue()


def test_extract_jpeg_dimensions() -> None:
    data = _make_test_jpeg(width=640, height=480)
    extracted = extract_image_metadata(data, "IMG-001")

    assert extracted.plane_container is not None
    container = extracted.plane_container
    assert container.image_width == 640
    assert container.image_height == 480


def test_extract_jpeg_exif() -> None:
    data = _make_test_jpeg(dt_original="2023:05:10 14:30:00")
    extracted = extract_image_metadata(data, "IMG-002")

    container = extracted.plane_container
    assert container is not None
    assert container.camera_make == "TestCamera"
    assert container.camera_model == "TC-1"
    assert container.software == "TestSoftware 1.0"
    assert "DateTimeOriginal" in container.exif

    temporal = extracted.plane_temporal
    assert temporal is not None
    assert temporal.earliest == datetime(2023, 5, 10, 14, 30, 0, tzinfo=UTC)


def test_extract_png_stdlib_fallback() -> None:
    """Verify PNG parsing works even without Pillow by using raw bytes."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow not installed")

    img = Image.new("RGBA", (120, 60), color=(0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()

    extracted = extract_image_metadata(data, "IMG-003")
    container = extracted.plane_container
    assert container is not None
    assert container.image_width == 120
    assert container.image_height == 60
    assert container.format_version == "PNG"


def test_extract_image_no_pillow_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force stdlib fallback path by hiding PIL."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow not installed")

    import anchorum.forensic.core.extraction.image as image_mod

    monkeypatch.setattr(image_mod, "_extract_with_pillow", lambda _data: None)

    data = _make_test_jpeg(width=200, height=150)
    extracted = extract_image_metadata(data, "IMG-004")
    container = extracted.plane_container
    assert container is not None
    assert container.image_width == 200
    assert container.image_height == 150


def test_extract_image_invalid_data() -> None:
    extracted = extract_image_metadata(b"not an image", "IMG-BAD")
    assert extracted.plane_container is None
    assert len(extracted.extraction_errors) == 1
