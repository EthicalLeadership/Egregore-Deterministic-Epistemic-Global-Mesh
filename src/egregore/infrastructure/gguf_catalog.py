"""
GGUF Model Catalog — canonical directory structure and metadata tracking.
"""

import contextlib
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MODELS_ROOT = Path(
    os.environ.get(
        "BLACKSTAR_MODELS_ROOT",
        os.environ.get("MODELS_DIR", "/opt/egregore/models"),
    )
)
GGUF_ROOT = MODELS_ROOT / "gguf"
EXPERT_DIR = GGUF_ROOT / "expert"
GENERAL_DIR = GGUF_ROOT / "general"
SPECIALIZED_DIR = GGUF_ROOT / "specialized"
CATALOG_FILE = GGUF_ROOT / ".catalog.json"


@dataclass
class GGUFEntry:
    model_id: str
    filename: str
    tier: str  # expert | general | specialized
    quantization: str  # Q4_K_M, Q5_K_M, Q8_0, etc.
    parameters: str  # e.g. "7B", "13B", "70B"
    size_bytes: int
    sha256: str
    capabilities: list[str] = field(default_factory=list)
    installed_at: str = ""
    last_verified: str = ""


class GGUFCatalog:
    def __init__(self):
        self._ensure_dirs()
        self._entries: dict[str, GGUFEntry] = {}
        self._load()

    def _ensure_dirs(self):
        for d in [MODELS_ROOT, GGUF_ROOT, EXPERT_DIR, GENERAL_DIR, SPECIALIZED_DIR]:
            try:
                d.mkdir(parents=True, mode=0o755, exist_ok=True)
            except OSError:
                # Read-only or restricted namespace; ignore if directory exists.
                if not d.exists():
                    raise

    def _load(self):
        if CATALOG_FILE.exists():
            with open(CATALOG_FILE) as f:
                raw = json.load(f)
                entries = raw.get("entries", {})
                for k, v in entries.items():
                    self._entries[k] = GGUFEntry(**v)

    def _save(self):
        raw = {
            "meta": {"version": "1.0", "node": "pioneer1", "last_updated": ""},
            "entries": {k: asdict(v) for k, v in self._entries.items()},
        }
        try:
            with open(CATALOG_FILE, "w") as f:
                json.dump(raw, f, indent=2)
        except OSError:
            # In a restricted service namespace (e.g. systemd ProtectSystem)
            # the catalog may be read-only. Verification should still succeed.
            pass

    def register(self, entry: GGUFEntry) -> None:
        target_dir = GGUF_ROOT / entry.tier
        target_dir / entry.filename
        entry.installed_at = datetime.now(UTC).isoformat()
        entry.last_verified = entry.installed_at
        self._entries[entry.model_id] = entry
        self._save()

    def get(self, model_id: str) -> GGUFEntry | None:
        return self._entries.get(model_id)

    def list_models(self) -> list[str]:
        """Return registered model IDs."""
        return list(self._entries.keys())

    def list_by_tier(self, tier: str) -> list[GGUFEntry]:
        return [e for e in self._entries.values() if e.tier == tier]

    def verify_all(self) -> dict[str, str]:
        results = {}
        for model_id, entry in self._entries.items():
            target_path = GGUF_ROOT / entry.tier / entry.filename
            if not target_path.exists():
                results[model_id] = "MISSING"
                continue
            h = hashlib.sha256()
            with open(target_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            if h.hexdigest() == entry.sha256:
                results[model_id] = "VERIFIED"
                entry.last_verified = datetime.now(UTC).isoformat()
            else:
                results[model_id] = "CORRUPT"
        with contextlib.suppress(OSError):
            self._save()
        return results

    def health_check(self) -> dict:
        total = len(self._entries)
        missing = sum(
            1
            for e in self._entries.values()
            if not (GGUF_ROOT / e.tier / e.filename).exists()
        )
        return {
            "status": "HEALTHY" if missing == 0 else "DEGRADED",
            "total_models": total,
            "missing": missing,
            "catalog_path": str(CATALOG_FILE),
            "tiers": {
                "expert": len(self.list_by_tier("expert")),
                "general": len(self.list_by_tier("general")),
                "specialized": len(self.list_by_tier("specialized")),
            },
        }


def run_gguf_health_check() -> dict:
    """ANCHORUM hook."""
    catalog = GGUFCatalog()
    return catalog.health_check()
