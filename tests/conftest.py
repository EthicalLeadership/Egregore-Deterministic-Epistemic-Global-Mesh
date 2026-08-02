import os
import tempfile
from pathlib import Path

import pytest

# Ensure tests never write to the real data directory.
_tmp_root = tempfile.mkdtemp(prefix="egregore_test_")
os.environ.setdefault("EGREGORE_DATA_DIR", _tmp_root)
os.environ.setdefault("EGREGORE_NODE_ID", "testnode")
os.environ.setdefault("EGREGORE_API_KEYS", "a" * 64 + ":test:admin:admin")
os.environ.setdefault("EGREGORE_ZARC_SIGNING_KEY_HEX", "a" * 64)

# Redirect repo-root defaults to the actual checkout so tests can create
# directories (rag/, cells/, ANCHORUM_reports/) without touching /opt/egregore.
_repo_root = str(Path(__file__).resolve().parent.parent)
os.environ.setdefault("EGREGORE_REPO_ROOT", _repo_root)
os.environ.setdefault(
    "ANCHORUM_REPORT_DIR", os.path.join(_repo_root, "ANCHORUM_reports")
)


@pytest.fixture(autouse=True)
def _fake_vram(monkeypatch: pytest.MonkeyPatch):
    """Tests never touch the real GPU: pre-flight always sees headroom."""
    from egregore.factory import residency as residency_mod

    monkeypatch.setattr(residency_mod, "vram_free_mb", lambda: 8000)
    # Reset the process-wide residency singleton so host bindings stay fresh.
    try:
        from egregore.interface import factory_router

        factory_router._residency = None
    except ImportError:
        pass
    yield
os.environ.setdefault(
    "ANCHORUM_ZARC_PATH", os.path.join(_tmp_root, "anchorum", "reports", "zarc")
)
os.environ.setdefault("EGREGORE_MODELS_ROOT", os.path.join(_tmp_root, "models"))

from egregore.kernel.ed25519_signer import generate_signing_key, get_verify_key_hex


@pytest.fixture(scope="session")
def signing_key() -> str:
    return generate_signing_key()


@pytest.fixture(scope="session")
def verify_key(signing_key: str) -> str:
    return get_verify_key_hex(signing_key)


@pytest.fixture
def user_repository(tmp_path: Path):
    """Provide a fresh SQLite user repository for a single test."""
    from egregore.infrastructure.persistence.user_repository import (
        SQLiteUserRepository,
    )

    db_path = tmp_path / "node.db"
    return SQLiteUserRepository(str(db_path))
