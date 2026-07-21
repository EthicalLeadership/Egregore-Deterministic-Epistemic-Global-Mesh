"""ZARC configuration — fail-closed. Reads from env or Docker secret files."""

import os


def _load_key() -> str:
    """Load signing key from env var or Docker secret file."""
    # Priority 1: Direct env var
    key = os.environ.get("BLACKSTAR_ZARC_SIGNING_KEY_HEX", "")
    if key and len(key) == 64:
        return key

    # Priority 2: Docker secret file (env var ending in _FILE)
    key_file = os.environ.get("BLACKSTAR_ZARC_SIGNING_KEY_HEX_FILE", "")
    if key_file and os.path.exists(key_file):
        with open(key_file) as f:
            key = f.read().strip()
        if key and len(key) == 64:
            return key

    # Fail closed
    raise RuntimeError(
        "BLACKSTAR_ZARC_SIGNING_KEY_HEX is mandatory and must be 64 hex chars. "
        "Set it via environment variable or Docker secret. No fallback key exists."
    )


ZARC_SIGNING_KEY_HEX = _load_key()
