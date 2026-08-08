"""ANCHORUM filesystem path helpers."""

from __future__ import annotations

import os
from pathlib import Path

ANCHORUM_ZARC_DIR = Path(
    os.environ.get("ANCHORUM_ZARC_PATH", "/var/lib/anchorum/reports/zarc")
)


def anchorum_zarc_dir(case_id: str) -> Path:
    """Return the per-case .zarc audit directory."""
    path = ANCHORUM_ZARC_DIR / case_id
    path.mkdir(parents=True, exist_ok=True)
    return path
