"""Canonical filesystem locations for the Egregore repo."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Return the Egregore repo root.

    ``EGREGORE_REPO_ROOT`` wins when set (the explicit override, e.g. the
    ``.env`` pin on pioneer1). Otherwise the root is derived from this file's
    own location, so the correct answer is automatic on any node and no
    hardcoded deployment path (``/opt/egregore``) is required.
    """
    if env_root := os.environ.get("EGREGORE_REPO_ROOT"):
        return Path(env_root)
    # src/egregore/paths.py -> egregore/ -> src/ -> repo root
    return Path(__file__).resolve().parents[2]
