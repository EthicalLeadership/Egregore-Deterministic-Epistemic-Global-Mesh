"""PDF Liberation Engine (CUSTOM-008).

Wraps qpdf (Apache 2.0) to strip owner passwords and permission restrictions,
producing a clean, unrestricted derivative while preserving content and metadata.

Governed by CBI-0:
- M1: Original file read-only; output written to separate directory.
- M2: Tool registered in capability manifest.
- M3: Output captured to .zarc; no re-entry.
- M4: Spec/runtime equivalence audit emitted.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from anchorum.forensic.core.manifest import register_tool
from anchorum.forensic.core.provenance import ZarcEventType, emit_zarc_event
from anchorum.forensic.core.shell import _run_external
from anchorum.forensic.core.validation import validate_input_size

# ---------------------------------------------------------------------------
# 1. Tool registration (M2)
# ---------------------------------------------------------------------------
register_tool(
    name="pdf_liberation_engine",
    version="1.0.0",
    plane="Plane 1",
    description="Remove owner restrictions from PDF using qpdf",
    dependencies=["qpdf (>=11.9)"],
    license="ANCHORUM proprietary (wraps Apache 2.0 qpdf)",
)


# ---------------------------------------------------------------------------
# 2. Public API
# ---------------------------------------------------------------------------
def liberate(
    *,
    input_pdf: Path,
    password: str | None,
    output_dir: Path,
    case_id: str,
    operator: str,
) -> dict[str, Any]:
    """Strip encryption and owner restrictions from a PDF.

    Args:
        input_pdf: Path to the locked PDF (read-only).
        password: Owner password if known (e.g., supplied by sender).
        output_dir: Directory where clean.pdf and manifest will be written.
        case_id: Case identifier for provenance tracking.
        operator: Operator username for audit trail.

    Returns:
        Dict with keys:
            original_hash, clean_hash, clean_path, restrictions_before,
            restrictions_after, encryption_removed, manifest_path

    Raises:
        FileNotFoundError: if input_pdf does not exist.
        ValueError: if qpdf cannot decrypt (bad password or unsupported encryption).
        RuntimeError: for other qpdf failures.

    """
    if not input_pdf.exists():
        raise FileNotFoundError(f"Input PDF not found: {input_pdf}")

    validate_input_size(input_pdf, label="input_pdf")

    # CBI-0 M1: Verify original is read-only (at OS level we trust permissions;
    # this is a double-check in code).
    if not os.access(input_pdf, os.R_OK) or os.access(input_pdf, os.W_OK):
        raise PermissionError(f"Original file must be read-only: {input_pdf}")

    # 2.1 Hash original
    original_hash = _sha256_file(input_pdf)

    # 2.2 Prepare deterministic output path
    timestamp_ns = _derive_timestamp_ns(original_hash, case_id)
    clean_filename = f"{original_hash[:16]}_{timestamp_ns}.pdf"
    clean_path = output_dir / clean_filename
    output_dir.mkdir(parents=True, exist_ok=True)

    # 2.3 Call qpdf (M3: output captured, not re-entered)
    _run_qpdf(
        input_pdf=input_pdf,
        output_pdf=clean_path,
        password=password,
    )

    # 2.4 Hash clean output
    clean_hash = _sha256_file(clean_path)

    # 2.5 Build restriction report (compare before/after using pikepdf)
    restrictions_before = _extract_restrictions(input_pdf, password)
    restrictions_after = _extract_restrictions(clean_path, None)

    # 2.6 Generate manifest
    manifest_path = output_dir / f"liberation_manifest_{timestamp_ns}.json"
    manifest = {
        "original_hash": original_hash,
        "clean_hash": clean_hash,
        "clean_path": str(clean_path),
        "manifest_path": str(manifest_path),
        "restrictions_before": restrictions_before,
        "restrictions_after": restrictions_after,
        "encryption_removed": restrictions_before != restrictions_after
        or restrictions_after == ["none"],
        "operator": operator,
        "timestamp_ns": timestamp_ns,
        "qpdf_exit_code": 0,  # will be populated by _run_qpdf; here we know success
    }

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # 2.7 Emit .zarc event (M4 audit)
    emit_zarc_event(
        event_type=ZarcEventType.PDF_LIBERATION,
        case_id=case_id,
        operator=operator,
        payload={
            "original_hash": original_hash,
            "clean_hash": clean_hash,
            "manifest_path": str(manifest_path),
            "restrictions_before": restrictions_before,
            "restrictions_after": restrictions_after,
        },
    )

    return manifest


# ---------------------------------------------------------------------------
# 3. Internal helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _derive_timestamp_ns(original_hash: str, case_id: str) -> int:
    """Deterministic timestamp_ns derived from original hash + case_id.
    Uses hash to produce a stable but unique integer.
    """
    seed = f"{original_hash}:{case_id}:liberation"
    digest = hashlib.sha256(seed.encode()).digest()
    # Use first 8 bytes as nanosecond epoch (within range)
    return int.from_bytes(digest[:8], "big") % (10**18)


def _run_qpdf(
    input_pdf: Path,
    output_pdf: Path,
    password: str | None = None,
) -> None:
    """Execute qpdf with strict error handling."""
    cmd = ["qpdf", "--decrypt"]
    if password:
        cmd.append(f"--password={password}")
    cmd.append(str(input_pdf))
    cmd.append(str(output_pdf))

    result = _run_external(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "invalid password" in stderr.lower() or "cannot decrypt" in stderr.lower():
            raise ValueError(f"PDF decryption failed (bad password?): {stderr}")
        raise RuntimeError(f"qpdf failed (exit {result.returncode}): {stderr}")

    # CBI-0 M3: do not feed qpdf output back into any decision logic


def _extract_restrictions(pdf_path: Path, password: str | None) -> list[str]:
    """Use pikepdf to list permission restrictions.
    Returns list of restriction names (e.g., 'print', 'modify') or ['none'].
    """
    try:
        import pikepdf

        with pikepdf.open(str(pdf_path), password=password or "") as pdf:
            if pdf.is_encrypted:
                # Still encrypted -> report all restrictions active
                return [
                    "print",
                    "copy",
                    "modify",
                    "annotate",
                    "accessibility",
                    "assemble",
                ]
            # Check permissions bits
            perm = pdf.allow
            restrictions: list[str] = []
            if not (
                getattr(perm, "print_lowres", False)
                or getattr(perm, "print_highres", False)
            ):
                restrictions.append("print")
            if not getattr(perm, "extract", False):
                restrictions.append("copy")
            if not getattr(perm, "modify_other", False):
                restrictions.append("modify")
            if not getattr(perm, "modify_annotation", False):
                restrictions.append("annotate")
            if not getattr(perm, "accessibility", False):
                restrictions.append("accessibility")
            return sorted(restrictions) if restrictions else ["none"]
    except Exception:  # noqa: BLE001
        # If we can't open, assume full restrictions
        return ["print", "copy", "modify", "annotate", "accessibility", "assemble"]
