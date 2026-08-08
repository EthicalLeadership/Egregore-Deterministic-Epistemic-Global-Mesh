"""Steganography Statistical Detector — Elite Release.

Detects hidden data in digital images using statistical analysis
(LBS chi‑square, entropy profiling) and optional external tool
correlation (steghide, zsteg) called exclusively via subprocess.

Fully CBI‑0 governed (M1‑M4). Returns an EventReference to the
immutable .zarc report. Plane‑2 (non‑deterministic).

External tools (steghide, zsteg) are GPL/MIT respectively; they
are never linked, only executed in separate processes.
"""

from __future__ import annotations

import hashlib
import logging
import math
import struct
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

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
CHI_SQUARE_THRESHOLD = 0.95  # p‑value below which LSB is non‑uniform
ENTROPY_THRESHOLD = 7.5  # bits per byte (8 is max)
VISUAL_LSB_PLANE_FILENAME = "lsb_plane.pgm"

# ---------------------------------------------------------------------------
# 2. Tool registration (M2)
# ---------------------------------------------------------------------------
register_tool(
    name="steganography_statistical_detector",
    version="1.0.0",
    plane="Plane 2",
    description="Statistical steganography detection with optional external tools",
    dependencies=[
        "Pillow (optional, MIT/HPND) for PNG/JPEG",
        "steghide (optional, GPL, subprocess only)",
        "zsteg (optional, MIT, subprocess only)",
    ],
    license="ANCHORUM proprietary (wraps optional external tools via subprocess)",
)


# ---------------------------------------------------------------------------
# 3. Data Structures
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LsbAnalysis:
    chi_square_statistic: float
    p_value: float
    embedded_data_detected: bool
    message: str


@dataclass(frozen=True)
class EntropyAnalysis:
    global_entropy: float
    local_entropy_variance: float
    suspicious_regions: tuple[tuple[int, int, int, int], ...]  # (x, y, w, h)


@dataclass(frozen=True)
class ToolResult:
    tool: str
    detected: bool
    output: str  # raw stdout
    error: str | None = None


@dataclass(frozen=True)
class StegoReport:
    """Immutable output of the pipeline."""

    original_hash: str
    lsb_analysis: LsbAnalysis
    entropy_analysis: EntropyAnalysis
    visual_attack_path: str | None
    tool_results: tuple[ToolResult, ...]
    confidence: str  # HIGH / MEDIUM / LOW


# ---------------------------------------------------------------------------
# 4. Ports (for external tools)
# ---------------------------------------------------------------------------
class StegoToolPort(ABC):
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def detect(self, image_path: Path) -> ToolResult: ...


class SteghideTool(StegoToolPort):
    def name(self) -> str:
        return "steghide"

    def detect(self, image_path: Path) -> ToolResult:
        try:
            result = _run_external(
                ["steghide", "info", str(image_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            # steghide returns 0 if embedded data found, 1 if not, non‑zero otherwise
            detected = result.returncode == 0
            return ToolResult(
                self.name(),
                detected,
                result.stdout.strip(),
                result.stderr.strip() or None,
            )
        except FileNotFoundError:
            return ToolResult(self.name(), False, "", "steghide not installed")
        except Exception as exc:  # noqa: BLE001
            logger.debug("steghide detection failed: %s", exc)
            return ToolResult(self.name(), False, "", str(exc))


class ZstegTool(StegoToolPort):
    def name(self) -> str:
        return "zsteg"

    def detect(self, image_path: Path) -> ToolResult:
        try:
            result = _run_external(
                ["zsteg", str(image_path)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            # zsteg returns 0 if something suspicious found, 1 if nothing
            detected = result.returncode == 0
            return ToolResult(
                self.name(),
                detected,
                result.stdout.strip(),
                result.stderr.strip() or None,
            )
        except FileNotFoundError:
            return ToolResult(self.name(), False, "", "zsteg not installed")
        except Exception as exc:  # noqa: BLE001
            logger.debug("zsteg detection failed: %s", exc)
            return ToolResult(self.name(), False, "", str(exc))


# ---------------------------------------------------------------------------
# 5. Stdlib Image Parsing (simple PNG/BMP/PPM)
# ---------------------------------------------------------------------------
def _read_rgb_pixels(
    image_path: Path,
) -> tuple[list[tuple[int, int, int]], int, int]:
    """Read RGB pixels from an image file.

    Uses Pillow if available (MIT/HPND), otherwise falls back to a
    stdlib-only BMP/PPM parser.
    """
    try:
        from PIL import Image

        img: Any = Image.open(image_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        # get_flattened_data() avoids the Pillow 11+ deprecation of getdata().
        try:
            pixels = list(img.get_flattened_data())
        except AttributeError:
            pixels = list(img.getdata())
        return pixels, img.width, img.height
    except ImportError:
        # fallback to BMP/PPM
        return _read_bmp_or_ppm(image_path)


def _read_bmp_or_ppm(
    path: Path,
) -> tuple[list[tuple[int, int, int]], int, int]:
    """Parse uncompressed BMP or P6 PPM."""
    with open(path, "rb") as f:
        header = f.read(2)
        if header == b"BM":
            return _read_bmp(f)
        elif header == b"P6":
            return _read_ppm(f)
        else:
            raise ValueError(
                "Unsupported image format for stdlib stego analysis. "
                "Install Pillow for PNG/JPEG support."
            )


def _read_bmp(f: BinaryIO) -> tuple[list[tuple[int, int, int]], int, int]:
    f.seek(18)
    width = struct.unpack("<I", f.read(4))[0]
    height = struct.unpack("<I", f.read(4))[0]
    f.seek(28)
    bpp = struct.unpack("<H", f.read(2))[0]
    if bpp != 24:
        raise ValueError("Only 24‑bit BMP supported for stdlib stego analysis")
    f.seek(54)  # data offset
    row_size = (width * 3 + 3) & ~3
    pixels = []
    for _ in range(height):
        row_data = f.read(row_size)[: width * 3]
        for x in range(0, width * 3, 3):
            b, g, r = row_data[x], row_data[x + 1], row_data[x + 2]
            pixels.append((r, g, b))
    return pixels, width, height


def _read_ppm(f: BinaryIO) -> tuple[list[tuple[int, int, int]], int, int]:
    line = f.readline()
    while line.startswith(b"#"):
        line = f.readline()
    dims = line
    maxval_line = f.readline()
    width, height = map(int, dims.split())
    maxval = int(maxval_line)
    data = f.read()
    if maxval <= 255:
        pixels = [(data[i], data[i + 1], data[i + 2]) for i in range(0, len(data), 3)]
    else:
        # 16‑bit PPM (rare)
        pixels = [
            (
                data[i] << 8 | data[i + 1],
                data[i + 2] << 8 | data[i + 3],
                data[i + 4] << 8 | data[i + 5],
            )
            for i in range(0, len(data), 6)
        ]
    return pixels, width, height


# ---------------------------------------------------------------------------
# 6. Chi‑Square LSB Analysis
# ---------------------------------------------------------------------------
def _lsb_chi_square(pixels: list[tuple[int, int, int]]) -> LsbAnalysis:
    """Test if the LSB of colour channels follows a uniform distribution.

    If hidden data is embedded, adjacent colour pairs (differing only in LSB)
    become more equal in frequency, which is detected by chi‑square.
    """
    # Aggregate all LSB pairs (0-1, 2-3, ... 254-255)
    evens: Counter[int] = Counter()
    odds: Counter[int] = Counter()
    for r, g, b in pixels:
        for val in (r, g, b):
            pair_idx = val & 0xFE  # zero out LSB
            if val & 1:
                odds[pair_idx] += 1
            else:
                evens[pair_idx] += 1

    # Expected frequencies: sum of each pair / 2
    chi_sq = 0.0
    degrees_of_freedom = 0
    for pair_idx in range(0, 256, 2):
        observed_even = evens.get(pair_idx, 0)
        observed_odd = odds.get(pair_idx, 0)
        total = observed_even + observed_odd
        if total == 0:
            continue
        expected = total / 2.0
        chi_sq += (observed_even - expected) ** 2 / expected
        chi_sq += (observed_odd - expected) ** 2 / expected
        degrees_of_freedom += 1

    if degrees_of_freedom == 0:
        return LsbAnalysis(0.0, 1.0, False, "No data")

    p_value = 1.0 - _chi2_cdf(chi_sq, degrees_of_freedom)
    detected = p_value < (1 - CHI_SQUARE_THRESHOLD)
    message = (
        "LSB distribution is non‑uniform — possible embedded data."
        if detected
        else "LSB distribution appears uniform."
    )
    return LsbAnalysis(chi_sq, p_value, detected, message)


def _chi2_cdf(x: float, k: int) -> float:
    """Compute CDF of chi‑square distribution using regularized lower incomplete gamma."""
    if x <= 0:
        return 0.0
    return _regularized_gamma_p(k / 2.0, x / 2.0)


def _regularized_gamma_p(s: float, x: float) -> float:
    """Lower regularized gamma function P(s, x) = γ(s, x) / Γ(s)."""
    if x < s + 1:
        # Series
        sum_ = 1.0 / s
        term = 1.0 / s
        for n in range(1, 200):
            term *= x / (s + n)
            sum_ += term
            if abs(term) < 1e-15:
                break
        return sum_ * math.exp(-x + s * math.log(x) - math.lgamma(s))
    else:
        # Continued fraction
        a = 1.0 - s
        b = a + x + 1.0
        f = 0.0
        c = 1.0
        d = 1.0 / b
        for i in range(1, 200):
            an = i * (s - i)
            b += 2.0
            d = an * d + b
            if abs(d) < 1e-30:
                d = 1e-30
            c = b + an / c
            if abs(c) < 1e-30:
                c = 1e-30
            d = 1.0 / d
            delta = d * c
            f *= delta
            if abs(delta - 1.0) < 1e-15:
                break
        return 1.0 - f * math.exp(-x + s * math.log(x) - math.lgamma(s))


# ---------------------------------------------------------------------------
# 7. Entropy Analysis
# ---------------------------------------------------------------------------
def _entropy_analysis(
    pixels: list[tuple[int, int, int]], width: int, height: int
) -> EntropyAnalysis:
    """Compute global Shannon entropy and local variance."""
    bytes_list = _flatten_pixels(pixels)
    if not bytes_list:
        return EntropyAnalysis(0.0, 0.0, ())

    entropy = _shannon_entropy(bytes_list)
    local_entropies, suspicious_regions = _local_entropy_map(
        pixels, width, height, block_size=32
    )

    variance = 0.0
    if local_entropies:
        mean = sum(local_entropies) / len(local_entropies)
        variance = sum((e - mean) ** 2 for e in local_entropies) / len(local_entropies)

    return EntropyAnalysis(entropy, variance, tuple(suspicious_regions))


def _flatten_pixels(pixels: list[tuple[int, int, int]]) -> list[int]:
    bytes_list: list[int] = []
    for r, g, b in pixels:
        bytes_list.extend((r, g, b))
    return bytes_list


def _shannon_entropy(data: list[int]) -> float:
    counts = Counter(data)
    total = len(data)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log2(p)
    return entropy


def _local_entropy_map(
    pixels: list[tuple[int, int, int]],
    width: int,
    height: int,
    block_size: int,
) -> tuple[list[float], list[tuple[int, int, int, int]]]:
    local_entropies: list[float] = []
    suspicious_regions: list[tuple[int, int, int, int]] = []
    for y0 in range(0, height, block_size):
        for x0 in range(0, width, block_size):
            block = _extract_block_bytes(pixels, width, height, x0, y0, block_size)
            if not block:
                continue
            ent = _shannon_entropy(block)
            local_entropies.append(ent)
            if ent > ENTROPY_THRESHOLD:
                suspicious_regions.append(
                    (x0, y0, min(block_size, width - x0), min(block_size, height - y0))
                )
    return local_entropies, suspicious_regions


def _extract_block_bytes(
    pixels: list[tuple[int, int, int]],
    width: int,
    height: int,
    x0: int,
    y0: int,
    block_size: int,
) -> list[int]:
    block_bytes: list[int] = []
    bh = min(block_size, height - y0)
    bw = min(block_size, width - x0)
    for dy in range(bh):
        for dx in range(bw):
            idx = (y0 + dy) * width + (x0 + dx)
            if idx < len(pixels):
                r, g, b = pixels[idx]
                block_bytes.extend((r, g, b))
    return block_bytes


# ---------------------------------------------------------------------------
# 8. Visual Attack (LSB plane)
# ---------------------------------------------------------------------------
def _generate_lsb_plane(
    pixels: list[tuple[int, int, int]], width: int, height: int, output_dir: Path
) -> str:
    """Render the LSB plane as a PGM image and save to disk."""
    pgm_path = output_dir / VISUAL_LSB_PLANE_FILENAME
    with open(pgm_path, "wb") as f:
        f.write(b"P5\n%d %d\n255\n" % (width, height))
        for y in range(height):
            for x in range(width):
                idx = y * width + x
                if idx >= len(pixels):
                    f.write(b"\x00")
                else:
                    r, g, b = pixels[idx]
                    # Combine LSBs into a grey value (0-7) scaled to 0-255
                    lsb_val = ((r & 1) << 2) | ((g & 1) << 1) | (b & 1)
                    grey = lsb_val * 36  # 0 → 0, 1→36, ..., 7→252
                    f.write(bytes([grey]))
    return str(pgm_path)


# ---------------------------------------------------------------------------
# 9. Public API
# ---------------------------------------------------------------------------
def detect_steganography(
    *,
    source: str | Path,
    case_id: str,
    operator: str,
    external_tools: list[StegoToolPort] | None = None,
) -> EventReference:
    """Analyse an image for steganographic content.

    Args:
        source: Path to image file (PNG, BMP, JPEG if Pillow available).
        case_id: Case identifier.
        operator: Operator username.
        external_tools: Optional list of external tool wrappers.

    Returns:
        EventReference to the sealed .zarc report.

    """
    original_path = Path(source)
    if not original_path.exists():
        raise FileNotFoundError(f"Original file not found: {original_path}")

    validate_input_size(original_path, label="stego_source")

    # Hash original
    original_hash = _sha256_file(original_path)

    # Read pixels (with Pillow if available, else stdlib BMP/PPM)
    pixels, width, height = _read_rgb_pixels(original_path)

    # Core analysis
    lsb = _lsb_chi_square(pixels)
    entropy = _entropy_analysis(pixels, width, height)

    # Visual attack
    output_dir = anchorum_zarc_dir(case_id) / "stego"
    output_dir.mkdir(parents=True, exist_ok=True)
    visual_path = _generate_lsb_plane(pixels, width, height, output_dir)

    # External tool correlation
    tool_results: list[ToolResult] = []
    tools = external_tools or [SteghideTool(), ZstegTool()]
    for tool in tools:
        tool_results.append(tool.detect(original_path))

    # Determine confidence
    confidence = "LOW"
    if lsb.embedded_data_detected and any(t.detected for t in tool_results):
        confidence = "HIGH"
    elif lsb.embedded_data_detected or entropy.global_entropy > ENTROPY_THRESHOLD:
        confidence = "MEDIUM"

    report = StegoReport(
        original_hash=original_hash,
        lsb_analysis=lsb,
        entropy_analysis=entropy,
        visual_attack_path=visual_path,
        tool_results=tuple(tool_results),
        confidence=confidence,
    )

    payload = {
        "original_hash": report.original_hash,
        "lsb_analysis": {
            "chi_square_statistic": report.lsb_analysis.chi_square_statistic,
            "p_value": report.lsb_analysis.p_value,
            "embedded_data_detected": report.lsb_analysis.embedded_data_detected,
            "message": report.lsb_analysis.message,
        },
        "entropy_analysis": {
            "global_entropy": report.entropy_analysis.global_entropy,
            "local_entropy_variance": report.entropy_analysis.local_entropy_variance,
            "suspicious_regions": list(report.entropy_analysis.suspicious_regions),
        },
        "visual_attack_path": report.visual_attack_path,
        "tool_results": [
            {
                "tool": t.tool,
                "detected": t.detected,
                "output": t.output,
                "error": t.error,
            }
            for t in report.tool_results
        ],
        "confidence": report.confidence,
        "case_id": case_id,
        "operator": operator,
        "timestamp_utc": datetime.now(UTC).isoformat(),
    }

    event_id = emit_zarc_event(
        event_type=ZarcEventType.STEGO_DETECTION,
        case_id=case_id,
        operator=operator,
        payload=payload,
    )
    return EventReference(event_id, anchorum_zarc_dir(case_id) / f"{event_id}.json")


def _sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()
