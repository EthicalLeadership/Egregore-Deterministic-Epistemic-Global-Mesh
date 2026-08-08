"""
ANCHORUM Image Metadata Extractor
==================================
Extracts image dimensions and EXIF metadata.

CBI-0 governed: read-only input, immutable output.

Pillow is used if installed; otherwise a stdlib fallback parses basic
JPEG/PNG headers for dimensions.
"""

from __future__ import annotations

import io
import struct
from datetime import UTC, datetime
from typing import Any

from anchorum.forensic.core.types import (
    ContainerMetadata,
    ContentMetadata,
    ExtractedMetadata,
    TemporalEvent,
    TemporalMetadata,
)

# ---------------------------------------------------------------------------
# 1. EXIF datetime tag helpers
# ---------------------------------------------------------------------------
_EXIF_DT_TAGS = {
    "DateTimeOriginal": "exif DateTimeOriginal",
    "DateTimeDigitized": "exif DateTimeDigitized",
    "DateTime": "exif DateTime",
}


def _parse_exif_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    # EXIF datetime format: "YYYY:MM:DD HH:MM:SS"
    try:
        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 2. Pillow-based extraction
# ---------------------------------------------------------------------------
def _sanitize_exif_value(value: Any) -> Any:
    """Convert Pillow EXIF values (IFDRational, bytes, tuples) to JSON-safe types."""
    # Pillow's IFDRational
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        try:
            return float(value)
        except Exception:
            return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, tuple):
        return tuple(_sanitize_exif_value(v) for v in value)
    if isinstance(value, list):
        return [_sanitize_exif_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize_exif_value(v) for k, v in value.items()}
    return value


def _extract_with_pillow(data: bytes) -> dict[str, Any] | None:  # noqa: C901
    try:
        from PIL import Image
        from PIL.ExifTags import Base
    except Exception:
        return None

    try:
        with Image.open(io.BytesIO(data)) as img:
            result: dict[str, Any] = {
                "format": img.format,
                "width": img.width,
                "height": img.height,
                "mode": img.mode,
            }

            exif_raw = img._getexif()
            if exif_raw:
                exif: dict[str, Any] = {}
                make: str | None = None
                model: str | None = None
                software: str | None = None
                lens: str | None = None
                orientation: int | None = None
                datetimes: dict[str, str] = {}

                for tag_id, value in exif_raw.items():
                    try:
                        tag_name = Base(tag_id).name
                    except Exception:
                        tag_name = str(tag_id)
                    exif[tag_name] = _sanitize_exif_value(value)

                    if tag_name == "Make":
                        make = str(value).strip() or None
                    elif tag_name == "Model":
                        model = str(value).strip() or None
                    elif tag_name == "Software":
                        software = str(value).strip() or None
                    elif tag_name == "LensModel":
                        lens = str(value).strip() or None
                    elif tag_name == "Orientation":
                        orientation = int(value) if isinstance(value, int) else None
                    elif tag_name in _EXIF_DT_TAGS:
                        datetimes[tag_name] = str(value)

                result["exif"] = exif
                result["camera_make"] = make
                result["camera_model"] = model
                result["software"] = software
                result["lens_model"] = lens
                result["orientation"] = orientation
                result["datetimes"] = datetimes

                # GPS
                gps_info = exif_raw.get(getattr(Base, "GPSInfo", None))
                if gps_info:
                    result["gps_info"] = {
                        k: _sanitize_exif_value(v) for k, v in gps_info.items()
                    }

            return result
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 3. Stdlib fallbacks
# ---------------------------------------------------------------------------
def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Parse JPEG SOF markers for width/height without Pillow."""
    i = 0
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1] if i + 1 < len(data) else 0
        # SOF0-SOF3, SOF5-SOF7, SOF9-SOF11, SOF13-SOF15
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            try:
                height = struct.unpack(">H", data[i + 5 : i + 7])[0]
                width = struct.unpack(">H", data[i + 7 : i + 9])[0]
                return width, height
            except Exception:
                return None
        # Skip segment length
        if marker == 0xD8 or marker == 0xD9 or marker == 0x00:
            i += 2
            continue
        if i + 3 >= len(data):
            break
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        i += 2 + length
    return None


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    """Parse PNG IHDR chunk for width/height."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    try:
        width = struct.unpack(">I", data[16:20])[0]
        height = struct.unpack(">I", data[20:24])[0]
        return width, height
    except Exception:
        return None


def _stdlib_image_info(data: bytes) -> dict[str, Any]:
    """Best-effort image info without Pillow."""
    result: dict[str, Any] = {}
    if data.startswith(b"\xff\xd8"):
        dims = _jpeg_dimensions(data)
        if dims:
            result["width"], result["height"] = dims
            result["format"] = "JPEG"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        dims = _png_dimensions(data)
        if dims:
            result["width"], result["height"] = dims
            result["format"] = "PNG"
    return result


# ---------------------------------------------------------------------------
# 4. Temporal plane
# ---------------------------------------------------------------------------
def _build_temporal_metadata(
    datetimes: dict[str, str],
    artifact_id: str,
) -> TemporalMetadata:
    events: list[TemporalEvent] = []
    for tag_name, raw in datetimes.items():
        ts = _parse_exif_datetime(raw)
        if ts:
            events.append(
                TemporalEvent(
                    timestamp=ts,
                    event_type=(
                        "creation" if tag_name == "DateTimeOriginal" else "modification"
                    ),
                    source_plane="container",
                    source_field=_EXIF_DT_TAGS.get(tag_name, f"exif.{tag_name}"),
                    raw_value=raw,
                    timezone="UTC",
                    confidence=1.0,
                    artifact_id=artifact_id,
                )
            )
    return TemporalMetadata(events=tuple(events))


# ---------------------------------------------------------------------------
# 5. Main extractor
# ---------------------------------------------------------------------------
def extract_image_metadata(
    data: bytes,
    artifact_id: str,
) -> ExtractedMetadata:
    """Extract all 5 metadata planes from an image file."""
    extraction_time = datetime.now(UTC)

    info = _extract_with_pillow(data)
    if info is None:
        info = _stdlib_image_info(data)

    if not info:
        return ExtractedMetadata(
            artifact_id=artifact_id,
            extraction_time=extraction_time,
            extraction_errors=("unable to parse image",),
        )

    exif = info.get("exif", {})
    datetimes = info.get("datetimes", {})

    container_metadata = ContainerMetadata(
        format_version=info.get("format"),
        image_width=info.get("width"),
        image_height=info.get("height"),
        color_space=info.get("mode"),
        orientation=info.get("orientation"),
        exif=exif,
        camera_make=info.get("camera_make"),
        camera_model=info.get("camera_model"),
        camera_serial=exif.get("BodySerialNumber"),
        lens_model=info.get("lens_model"),
        software=info.get("software"),
    )

    content_metadata = ContentMetadata(
        image_count=1,
    )

    temporal_metadata = _build_temporal_metadata(datetimes, artifact_id)

    return ExtractedMetadata(
        artifact_id=artifact_id,
        extraction_time=extraction_time,
        plane_container=container_metadata,
        plane_content=content_metadata,
        plane_temporal=temporal_metadata,
    )
