"""ANCHORUM hardened subprocess runner.

All external tool invocations go through `_run_external`. It resolves the
executable to an absolute path, validates it against an allow-list, and logs
failures. This keeps subprocess security warnings localized to a single
audited location.
"""

from __future__ import annotations

import logging
import shutil
import subprocess  # nosec B404
from pathlib import Path
from typing import Any, Literal, overload

logger = logging.getLogger(__name__)

# Allowed external binaries. Additions must be reviewed for license compliance.
_ALLOWED_BINARIES = {
    "qpdf",
    "tesseract",
    "steghide",
    "zsteg",
    "exiftool",
}


def _resolve_executable(name: str) -> Path:
    """Resolve an executable name to an absolute path."""
    resolved = shutil.which(name)
    if resolved is None:
        raise FileNotFoundError(f"Required external executable not found: {name}")
    path = Path(resolved).resolve()
    if path.name not in _ALLOWED_BINARIES:
        raise ValueError(f"Executable not in ANCHORUM allow-list: {path}")
    return path


@overload
def _run_external(
    argv: list[str],
    *,
    check: bool = False,
    timeout: float | None = None,
    text: Literal[True] = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]: ...


@overload
def _run_external(
    argv: list[str],
    *,
    check: bool = False,
    timeout: float | None = None,
    text: Literal[False] = False,
    **kwargs: Any,
) -> subprocess.CompletedProcess[bytes]: ...


def _run_external(
    argv: list[str],
    *,
    check: bool = False,
    timeout: float | None = None,
    text: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Run an allowed external executable with absolute path.

    Args:
        argv: Command line starting with the executable name.
        check: Whether to raise on non-zero exit status.
        timeout: Optional timeout in seconds.
        text: Whether to decode stdout/stderr as text.
        **kwargs: Additional arguments forwarded to ``subprocess.run``.

    Returns:
        The completed process.

    Raises:
        FileNotFoundError: If the executable is not installed.
        ValueError: If the executable is not in the allow-list.
        subprocess.CalledProcessError: If ``check`` is True and the process fails.

    """
    if not argv:
        raise ValueError("Empty command")
    executable = _resolve_executable(argv[0])
    cmd = [str(executable)] + argv[1:]
    # S603 is suppressed here because we validate the executable against an
    # explicit allow-list and resolve it to an absolute path before invoking.
    logger.debug("Running external command: %s", " ".join(cmd))
    return subprocess.run(  # noqa: S603
        cmd, check=check, timeout=timeout, text=text, **kwargs
    )  # nosec B603
